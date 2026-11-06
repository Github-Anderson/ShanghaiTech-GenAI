(() => {
    "use strict";
    const el = Object.fromEntries(["apiKey", "loadModels", "model", "stream", "newChat", "deleteChat", "counter", "transcript", "empty", "chatForm", "prompt", "stop", "send", "status", "details", "attachment", "upload", "clearAttachments", "attachmentStatus", "searchOption", "thinkingOption", "thinkingOptionLabel", "maxTokens", "systemPrompt", "includeUsage", "advancedJson", "modelLimits"].map(id => [id, document.getElementById(id)]));
    let capabilities = null;
    function refreshOptions(restore = false) {
      const model = capabilities?.models.find(row => row.id === el.model.value);
      if (restore) {
        el.thinkingOption.checked = false;
      }
      el.maxTokens.max = model?.max_output_tokens || '';
      el.maxTokens.placeholder = model ? '默认' : '先载入模型';
      if (restore) el.maxTokens.value = '';
      el.modelLimits.textContent = model ? `输出上限 ${model.max_output_tokens} 个词元；历史 ${capabilities.limits.max_history_messages ?? '不限'} 条；系统提示独立保留。` : '先载入模型和能力。';
      el.thinkingOptionLabel.hidden = !model?.thinking.supported;
      if (!model?.thinking.supported) el.thinkingOption.checked = false;
      if (!model?.search.supported) el.searchOption.checked = false;
      el.searchOption.disabled = running || !model?.search.supported;
      el.thinkingOption.disabled = running || !model?.thinking.supported;
      el.searchOption.title = '仅控制来源网站搜索；不控制 DSH 的搜索工具。';
    }
    el.model.addEventListener('change', () => { resetChat(false); });
    let history = [];
    let websiteConversation = crypto.randomUUID();
    let receivedEvents = [];
    let currentRequest = null;
    let controller = null;
    let running = false;
    let modelsLoading = false;
    let pendingAttachments = [];
    let attachmentDetails = [];

    function refreshAttachments() {
      const model = capabilities?.models.find(row => row.id === el.model.value);
      const images = model?.attachments.images;
      const docs = model?.attachments.documents;
      const allowed = [...(images?.supported ? images.mime_types : []), ...(docs?.supported ? docs.extensions : [])];
      el.attachment.accept = allowed.join(',');
      const mib = bytes => (bytes / 1024 / 1024).toLocaleString();
      el.attachmentStatus.textContent = pendingAttachments.length
        ? '待发送：' + pendingAttachments.map(file => file.filename + (file.model !== el.model.value ? '（模型不匹配）' : file.expires_at <= Date.now()/1000 ? '（已过期）' : '（可用）')).join('、')
        : model ? `图片：${images.supported ? images.mime_types.join(', ') + '，小于 ' + mib(images.max_bytes) + ' MiB' : '未开放'}。文档：${docs.supported ? docs.extensions.join(', ') + '，小于 ' + mib(docs.max_bytes) + ' MiB' : '未开放'}。有效期 ${capabilities.files.ttl_seconds} 秒，重启后需重传。` : '先载入模型以查看附件格式与限制。';
      refreshDetails();
    }

    function setStatus(message, isError = false) {
      el.status.textContent = message;
      el.status.classList.toggle("error", isError);
    }
    function refreshDetails() {
      el.counter.textContent = "历史 " + history.length + " 条，" + (capabilities?.limits?.max_history_messages ? "发送最近 " + capabilities.limits.max_history_messages + " 条" : "发送全部历史");
      el.details.value = JSON.stringify({ completed_messages: history, current_request: currentRequest, attachments: attachmentDetails, received_events: receivedEvents }, null, 2);
    }
    function setRunning(value) {
      running = value;
      window.gatewayUI.busy = value || modelsLoading;
      value = value || !!window.gatewayUI.managing;
      document.getElementById('importCredentials').disabled = value || modelsLoading;
      document.getElementById('logoutCredentials').disabled = value || modelsLoading;
      for (const field of [el.maxTokens, el.systemPrompt, el.includeUsage, el.advancedJson]) field.disabled = value || modelsLoading;
      const ready = !!capabilities?.models.some(model => model.id === el.model.value);
      el.send.disabled = value || modelsLoading || !ready;
      el.stop.disabled = !running;
      el.newChat.disabled = value;
      el.deleteChat.disabled = value || modelsLoading;
      el.model.disabled = value || modelsLoading || !capabilities;
      refreshOptions();
      el.stream.disabled = value;
      el.includeUsage.disabled = value || !el.stream.checked;
      el.loadModels.disabled = value || modelsLoading;
      el.apiKey.disabled = value || modelsLoading;
      el.upload.disabled = value || modelsLoading || !ready;
      el.attachment.disabled = value || modelsLoading || !ready;
      el.clearAttachments.disabled = value;
    }
    function scrollMessages() { el.transcript.scrollTop = el.transcript.scrollHeight; }
    function addMessage(role, text) {
      if (el.empty) el.empty.remove();
      const card = document.createElement("article");
      card.className = "message " + role;
      const label = document.createElement("div");
      label.className = "message-label";
      label.textContent = role === "user" ? "你" : "助手";
      const reasoning = document.createElement("div");
      reasoning.className = "reasoning";
      reasoning.hidden = true;
      const reasoningTitle = document.createElement("div");
      reasoningTitle.className = "reasoning-title";
      reasoningTitle.textContent = "推理内容";
      const reasoningBody = document.createElement("div");
      reasoningBody.className = "reasoning-body";
      reasoning.append(reasoningTitle, reasoningBody);
      const body = document.createElement("div");
      body.className = "message-body";
      body.textContent = text;
      const state = document.createElement("div");
      state.className = "message-state";
      const metadata = document.createElement('div');
      card.append(label, reasoning, body, metadata, state, document.createElement('hr'));
      el.transcript.append(card);
      scrollMessages();
      return { card, body, reasoning, reasoningBody, state, metadata };
    }
    function gatewayHeaders(key) { return { "Content-Type": "application/json", "Authorization": "Bearer " + key }; }
    function safeFailure(message) { const e = new Error(message); e.safeUiMessage = message; return e; }
    function noteEvent(value) { receivedEvents.push(value); refreshDetails(); }

    el.clearAttachments.addEventListener("click", () => { pendingAttachments = []; el.attachment.value = ""; refreshAttachments(); });
    el.upload.addEventListener("click", async () => {
      if (running || modelsLoading) return;
      const key = el.apiKey.value.trim();
      const files = Array.from(el.attachment.files);
      if (!key || !files.length) { setStatus("请填写网关密钥并选择附件。", true); return; }
      const model = capabilities.models.find(row => row.id === el.model.value);
      for (const file of files) {
        const isImage = model.attachments.images.mime_types.includes(file.type);
        const rule = isImage ? model.attachments.images : model.attachments.documents;
        const suffix = '.' + file.name.split('.').pop().toLowerCase();
        if (!rule.supported || (!isImage && !rule.extensions.includes(suffix)) || !file.size || file.size >= rule.max_bytes) {
          setStatus('附件类型、模型能力或大小不符合当前限制：' + file.name, true); return;
        }
      }
      controller = new AbortController();
      setRunning(true);
      setStatus("正在上传附件到网站，未发送聊天…");
      try {
        for(const file of files) {
        const form = new FormData();
        form.append("file", file);
        form.append("model", el.model.value);
        form.append("purpose", "user_data");
        form.append("conversation_id", websiteConversation);
        const response = await fetch("/v1/files", {method:"POST", headers:{Authorization:"Bearer " + key}, body:form, signal:controller.signal, credentials:"omit", cache:"no-store"});
        const data = await response.json();
        if (!response.ok || data.error) { noteEvent(data); throw safeFailure("附件上传失败，请查看请求与响应详情。"); }
        if(!pendingAttachments.some(item=>item.id===data.id))pendingAttachments.push({id:data.id, filename:data.filename, model:data.model, kind:data.kind, expires_at:data.expires_at});
        attachmentDetails.push(data);
        }
        el.attachment.value = "";
        refreshAttachments();
        setStatus("附件已上传。输入问题并发送后，将通过网页 fileIds/imageUrls 引用附件。");
      } catch (error) {
        setStatus(controller.signal.aborted ? "已停止等待上传；网站可能已经保留了附件。" : error.safeUiMessage || "附件上传未完成。", true);
      } finally { controller = null; setRunning(false); }
    });

    el.loadModels.addEventListener("click", async () => {
      const key = el.apiKey.value.trim();
      if (!key) { setStatus("请先输入本地网关密钥。", true); return; }
      modelsLoading = true;
      setRunning(false);
      el.loadModels.disabled = true;
      el.send.disabled = true;
      el.apiKey.disabled = true;
      setStatus("正在载入模型…");
      try {
        const response = await fetch("/v1/models?refresh=true", { headers: { "Authorization": "Bearer " + key }, cache: "no-store", credentials: "omit" });
        if (!response.ok) throw safeFailure("模型载入失败，请检查网关密钥和服务配置。");
        const data = await response.json();
        const capabilityResponse = await fetch('/v1/capabilities', {headers: {Authorization: 'Bearer ' + key}, cache: 'no-store', credentials: 'omit'});
        if (!capabilityResponse.ok) throw safeFailure('能力信息载入失败，请升级网关并重试。');
        capabilities = await capabilityResponse.json();
        if (!Array.isArray(capabilities.models)) throw safeFailure('能力信息格式无法识别。');
        if (!Array.isArray(data.data) || !data.data.length) throw safeFailure("网关没有返回可用模型，请检查模型配置。");
        const ids = data.data.filter(item => item && typeof item.id === "string" && item.id.length <= 120).map(item => item.id);
        if (!ids.length) throw safeFailure("模型列表格式无法识别。");
        const selected = el.model.value;
        el.model.replaceChildren();
          for (const id of ids) { const item = data.data.find(row => row.id === id); const option = document.createElement("option"); option.value = id; option.textContent = typeof item.name === 'string' ? item.name.slice(0, 256) : id; el.model.append(option); }
        if (ids.includes(selected)) el.model.value = selected;
          refreshOptions(true);
          refreshAttachments();
        setStatus("已载入 " + ids.length + " 个配置模型；这不表示已完成上游可用性验证。");
      } catch (error) {
        setStatus(error.safeUiMessage || "模型载入失败，请检查本地网关连接。", true);
      } finally {
        modelsLoading = false;
        setRunning(running);
      }
    });

    async function readSse(response, onData) {
      if (!response.body) throw safeFailure("响应没有可读取的流。");
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8", { fatal: true });
      let pending = "";
      let dataLines = [];
      let eventName = "message";
      let doneSeen = false;
      function dispatch() {
        if (!dataLines.length) { eventName = "message"; return; }
        const data = dataLines.join("\n");
        const name = eventName;
        dataLines = [];
        eventName = "message";
        if (data.trim() === "[DONE]") { doneSeen = true; noteEvent({ event: name, data: "[DONE]" }); return; }
        let parsed;
        try { parsed = JSON.parse(data); } catch { throw safeFailure("收到无法识别的兼容流事件，输出未完成。"); }
        noteEvent(parsed);
        if (name === "error" || (parsed && parsed.error)) throw safeFailure("网关返回流式错误；详细消息中保留了错误事件。");
        if (doneSeen) throw safeFailure("结束标记后仍有消息，输出未确认完成。");
        onData(parsed);
      }
      function line(value) {
        if (value === "") { dispatch(); return; }
        if (value.startsWith(":")) return;
        const colon = value.indexOf(":");
        const field = colon < 0 ? value : value.slice(0, colon);
        let contents = colon < 0 ? "" : value.slice(colon + 1);
        if (contents.startsWith(" ")) contents = contents.slice(1);
        if (field === "data") dataLines.push(contents);
        else if (field === "event") eventName = contents;
      }
      function consume(final = false) {
        while (pending.length) {
          const match = /[\r\n]/.exec(pending);
          if (!match) break;
          const index = match.index;
          if (!final && pending[index] === "\r" && index === pending.length - 1) break;
          const width = pending[index] === "\r" && pending[index + 1] === "\n" ? 2 : 1;
          line(pending.slice(0, index));
          pending = pending.slice(index + width);
        }
        if (final && pending) { line(pending); pending = ""; }
      }
      try {
        while (true) {
          const result = await reader.read();
          if (result.done) break;
          pending += decoder.decode(result.value, { stream: true });
          consume();
          if (doneSeen) { await reader.cancel(); return true; }
        }
        pending += decoder.decode();
        consume(true);
        dispatch();
        return doneSeen;
      } finally { reader.releaseLock(); }
    }

    el.chatForm.addEventListener("submit", async event => {
      event.preventDefault();
      if (running || modelsLoading) return;
      if (!capabilities?.models.some(model => model.id === el.model.value)) { setStatus("请先载入模型。", true); return; }
      const key = el.apiKey.value.trim();
      const prompt = el.prompt.value.trim();
      if (!key) { setStatus("请先输入本地网关密钥。", true); return; }
      if (!prompt) { setStatus("请输入消息。", true); return; }
      if (pendingAttachments.some(file => file.model !== el.model.value)) { setStatus("附件已绑定上传时的模型，请切回该模型或新建会话重新上传。", true); return; }
      if (pendingAttachments.some(file => file.expires_at <= Date.now()/1000)) { setStatus('附件引用已过期，请清除后重新上传。', true); refreshAttachments(); return; }
      const userMessage = { role: "user", content: pendingAttachments.length ? [{type:"text", text:prompt}, ...pendingAttachments.map(file => ({type:"file", file:{file_id:file.id}}))] : prompt };
      const selectedModel = el.model.value;
      const useStream = el.stream.checked;
      const payload = { model: selectedModel, messages: [...history, userMessage], stream: useStream, conversation_id: websiteConversation,
        shanghaitech: {search: el.searchOption.checked, thinking: el.thinkingOption.checked} };
      if (el.systemPrompt.value.trim()) payload.messages.unshift({role:'system', content:el.systemPrompt.value.trim()});
      if (el.maxTokens.value) {
        const limit = Number(el.maxTokens.value);
        if (!Number.isInteger(limit) || limit < 1 || limit > Number(el.maxTokens.max)) { setStatus('输出上限超出当前模型允许范围。', true); return; }
        payload.max_tokens = limit;
      }
      if (useStream) payload.stream_options = {include_usage: el.includeUsage.checked};
      try {
        const extra = JSON.parse(el.advancedJson.value.trim() || '{}');
        if (!extra || Array.isArray(extra) || typeof extra !== 'object' || Object.keys(extra).some(key => !['tools', 'tool_choice', 'store'].includes(key))) throw new Error();
        Object.assign(payload, extra);
      } catch { setStatus('高级参数须为 JSON 对象，只允许 tools、tool_choice、store。', true); return; }
      currentRequest = payload;
      receivedEvents = [];
      refreshDetails();
      addMessage("user", prompt);
      const assistant = addMessage("assistant", "");
      assistant.state.textContent = "等待响应…";
      el.prompt.value = "";
      controller = new AbortController();
      setRunning(true);
      setStatus("请求进行中。真实发送可能消耗网站额度。");
      let content = "";
      let reasoning = "";
      let finished = false;
      let searchProgress = '';
      let responseMeta = {};
      let toolCalls = [];
      let finishReason = '';
      function readMetadata(data) {
        if (data.usage) responseMeta.usage = data.usage;
        if (data.shanghaitech) responseMeta = {...responseMeta, ...data.shanghaitech};
        renderMetadata(assistant.metadata, responseMeta);
        const progress = data.shanghaitech?.search_progress;
        if (progress) searchProgress = progress.stage === 'searching' ? '正在搜索…' : '搜索完成' + (Number.isInteger(progress.count) ? '，' + progress.count + ' 条' : '');
      }
      function update() {
        multiline(assistant.body, content);
        multiline(assistant.reasoningBody, reasoning);
        assistant.reasoning.hidden = !reasoning;
        assistant.state.textContent = searchProgress || "接收中…";
        scrollMessages();
      }
      try {
        const response = await fetch("/v1/chat/completions", { method: "POST", headers: gatewayHeaders(key), body: JSON.stringify(payload), signal: controller.signal, credentials: "omit", cache: "no-store" });
        if (!response.ok) {
          try { const data = await response.json(); if (data && data.error) noteEvent(data); } catch { /* No response text is inserted into the UI. */ }
          throw safeFailure("网关拒绝了请求（HTTP " + response.status + "）。请查看详细错误并检查配置。");
        }
        if (useStream) {
          if (!(response.headers.get("content-type") || "").toLowerCase().includes("text/event-stream")) throw safeFailure("网关没有返回预期的流式内容类型。");
          const done = await readSse(response, data => {
            readMetadata(data);
            if (!data || !Array.isArray(data.choices)) throw safeFailure("兼容流消息格式无法识别。");
            for (const choice of data.choices) {
              if (choice.index !== 0 && choice.index !== undefined) continue;
              const delta = choice.delta || {};
              if (typeof delta.content === "string") content += delta.content;
              if (typeof delta.reasoning_content === "string") reasoning += delta.reasoning_content;
              if (delta.tool_calls) toolCalls.push(...delta.tool_calls);
              if (choice.finish_reason != null) { finished = true; finishReason = choice.finish_reason; }
            }
            update();
          });
          if (!done || !finished) throw safeFailure("流已断开，但没有完整结束标记；本次内容不加入后续上下文。");
        } else {
          const data = await response.json();
          noteEvent(data);
          readMetadata(data);
          if (data && data.error) throw safeFailure("网关返回错误；本次内容不加入后续上下文。");
          const choice = data && Array.isArray(data.choices) ? data.choices[0] : null;
          if (!choice || !choice.message || (typeof choice.message.content !== "string" && !choice.message.tool_calls) || choice.finish_reason == null) throw safeFailure("完整响应格式无法识别；本次内容不加入后续上下文。");
          content = choice.message.content || '';
          toolCalls = choice.message.tool_calls || [];
          finishReason = choice.finish_reason;
          reasoning = typeof choice.message.reasoning_content === "string" ? choice.message.reasoning_content : "";
          finished = true;
          update();
        }
        if (toolCalls.length) {
          assistant.state.textContent = '已收到工具调用；本页不执行工具，详细响应中可查看。';
          setStatus('工具调用测试完成；本轮未加入聊天上下文。');
          return;
        }
        if (!content.trim()) throw safeFailure("本次已结束但没有文本回答，详细内容仍保留；本次消息不加入后续上下文。");
        const historicalUser = {...userMessage};
        if (Array.isArray(historicalUser.content)) historicalUser.content = historicalUser.content.map(part => {
          const file = pendingAttachments.find(file => file.id === part.file?.file_id);
          return file?.kind === 'document' ? {type:'text', text:'【上传文件：' + file.filename + '】'} : part;
        });
        history.push(historicalUser, { role: "assistant", content });
        pendingAttachments = pendingAttachments.filter(file => file.kind === 'document');
        refreshAttachments();
        assistant.state.textContent = "已完成，已加入后续上下文" + (finishReason === "length" ? "（达到输出上限）" : "");
        setStatus("已完成。当前历史 " + history.length + " 条。");
        refreshDetails();
      } catch (error) {
        const cancelled = controller && controller.signal.aborted;
        assistant.state.textContent = cancelled ? "已停止，部分内容未加入后续上下文" : "未完成，本次内容未加入后续上下文";
        assistant.state.classList.add("failed");
        setStatus(cancelled ? "已停止读取。本次消息不加入后续上下文；这不保证上游停止计算或退款。" : (error.safeUiMessage || "请求未完成，请检查网关连接或详细错误。"), !cancelled);
      } finally {
        controller = null;
        setRunning(false);
        scrollMessages();
        el.prompt.focus();
      }
    });
    el.stream.addEventListener('change', () => setRunning(running));
    el.stop.addEventListener("click", () => { if (controller) controller.abort(); });
    function resetChat(selectFirst = true, focus = true) {
      if (running) return;
      if (selectFirst && el.model.options.length) el.model.selectedIndex = 0;
      refreshOptions(true);
      history = [];
      websiteConversation = crypto.randomUUID();
      pendingAttachments = [];
      attachmentDetails = [];
      el.attachment.value = "";
      refreshAttachments();
      receivedEvents = [];
      currentRequest = null;
      el.transcript.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "新会话已就绪。";
      el.transcript.append(empty);
      el.empty = empty;
      refreshDetails();
      setStatus("已清空当前页面的对话。原网站的历史记录不受影响。");
      if (focus) el.prompt.focus();
    }
    el.newChat.addEventListener("click", () => resetChat());
    el.deleteChat.addEventListener('click', async () => {
      if (running || modelsLoading) return;
      const key = el.apiKey.value.trim();
      if (!key) { setStatus('请先填写网关密钥。', true); return; }
      if (!window.confirm('删除当前会话及关联的网站历史？删除后不可恢复。')) return;
      setRunning(true);
      setStatus('正在删除会话…');
      try {
        const response = await fetch('/v1/conversations/' + encodeURIComponent(websiteConversation), {
          method: 'DELETE', headers: gatewayHeaders(key), signal: AbortSignal.timeout(330000)
        });
        const result = await response.json();
        if (!response.ok || result.deleted !== true) {
          throw safeFailure(result.error?.code === 'conversation_mapping_missing'
            ? '找不到完整的上游映射。旧会话或登录已变更的会话请到网站删除；当前消息已保留。'
            : '删除未完成，当前消息已保留。错误：' + (result.error?.code || 'delete_failed'));
        }
        setRunning(false);
        el.newChat.click();
        setStatus('会话已删除，网站确认清理 ' + result.deleted_groups + ' 个会话组。');
      } catch (error) {
        setStatus(error.safeUiMessage || '无法确认删除结果，当前消息已保留。请检查网关后重试。', true);
      } finally { setRunning(false); }
    });
    window.gatewayUI.credentialsChanged = () => {
      capabilities = null;
      el.model.replaceChildren(new Option('请重新载入模型', ''));
      resetChat(false, false);
      setRunning(false);
    };
    window.gatewayUI.syncControls = () => setRunning(running);
    window.gatewayUI.resetChat = () => resetChat(false);
    setRunning(false);
    el.prompt.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); el.chatForm.requestSubmit(); }
    });

    function multiline(target, text) {
      target.replaceChildren();
      text.split('\n').forEach((line, i) => { if (i) target.append(document.createElement('br')); target.append(document.createTextNode(line)); });
    }
    function renderMetadata(target, metadata) {
      target.replaceChildren();
      if (metadata.usage) {
        const p = document.createElement('p'); p.textContent = '用量：' + JSON.stringify(metadata.usage); target.append(p);
      }
      if (metadata.upstream_metadata) {
        const p = document.createElement('p'); p.textContent = '网站计数：' + JSON.stringify(metadata.upstream_metadata); target.append(p);
      }
      if (metadata.sources_status) {
        const p = document.createElement('p');
        p.textContent = '搜索来源：' + (metadata.sources_status === 'failed' ? '获取失败（' + (metadata.sources_error || 'unknown') + '）' : (metadata.sources?.links?.length ? '' : '本轮未返回链接'));
        target.append(p);
      }
      for (const link of metadata.sources?.links || []) {
        const p = document.createElement('p');
        try {
          const url = new URL(link.link);
          if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) continue;
          const a = document.createElement('a'); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = link.title;
          p.append(a, document.createTextNode(' ' + (link.snippet || ''))); target.append(p);
        } catch { /* Invalid URLs are not linked. */ }
      }
    }
})();
