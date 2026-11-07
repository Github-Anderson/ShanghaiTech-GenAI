// Model test page: capability-driven options, attachments and streamed chat.
import {$, api, element, gatewayFetch, gatewayKey, icon, on, session, setFlag, StaleError} from './core.js';

const state = {
  capabilities: null,
  history: [],
  conversation: crypto.randomUUID(),
  conversationUsed: false,
  receivedEvents: [],
  currentRequest: null,
  controller: null,
  running: false,
  modelsLoading: false,
  pendingAttachments: [],
  attachmentDetails: [],
};

const selectedModel = () => state.capabilities?.models.find(model => model.id === $('model').value);

function safeFailure(message) {
  const error = new Error(message);
  error.safeUiMessage = message;
  return error;
}

function setStatus(message, isError = false) {
  $('status').textContent = message;
  $('status').classList.toggle('error', isError);
}

function setEmpty(text) {
  const empty = element('p', {className: 'empty', text, attrs: {id: 'empty'}});
  $('transcript').replaceChildren(empty);
}

// ---------- Controls ----------
function refreshOptions(restore = false) {
  const model = selectedModel(), locked = state.running || session.managing;
  if (restore) {
    $('thinkingOption').checked = false;
    $('maxTokens').value = '';
  }
  $('maxTokens').max = model?.max_output_tokens || '';
  $('maxTokens').placeholder = model ? '默认' : '先载入模型';
  $('modelLimits').textContent = model
    ? `输出上限 ${model.max_output_tokens} 个词元；历史 ${state.capabilities.limits.max_history_messages ?? '不限'} 条；系统提示独立保留。`
    : '先载入模型和能力。';
  $('thinkingOptionLabel').hidden = !model?.thinking.supported;
  if (!model?.thinking.supported) $('thinkingOption').checked = false;
  if (!model?.search.supported) $('searchOption').checked = false;
  $('searchOption').disabled = locked || !model?.search.supported;
  $('thinkingOption').disabled = locked || !model?.thinking.supported;
  $('searchOption').title = '仅控制来源网站搜索；不控制 DSH 的搜索工具。';
}

function syncControls() {
  const {running, modelsLoading} = state;
  const locked = running || session.managing;
  const ready = Boolean(selectedModel());
  setFlag('busy', running || modelsLoading);
  for (const field of [$('maxTokens'), $('systemPrompt'), $('advancedJson')]) field.disabled = locked || modelsLoading;
  $('send').disabled = locked || modelsLoading || !ready;
  $('stop').disabled = !running;
  $('newChat').disabled = running;
  $('deleteChat').disabled = locked || modelsLoading || !state.conversationUsed;
  $('model').disabled = locked || modelsLoading || !state.capabilities;
  $('loadModels').disabled = locked || modelsLoading;
  $('stream').disabled = locked;
  $('includeUsage').disabled = locked || !$('stream').checked;
  $('attachment').disabled = locked || modelsLoading || !ready;
  $('clearAttachments').disabled = running || !state.pendingAttachments.length;
  $('transcript').setAttribute('aria-busy', String(running));
  refreshOptions();
}

function refreshDetails() {
  const limit = state.capabilities?.limits?.max_history_messages;
  $('counter').textContent = '历史 ' + state.history.length + ' 条，' + (limit ? '发送最近 ' + limit + ' 条' : '发送全部历史');
  $('details').value = JSON.stringify({
    completed_messages: state.history, current_request: state.currentRequest,
    attachments: state.attachmentDetails, received_events: state.receivedEvents,
  }, null, 2);
}

function noteEvent(value) {
  state.receivedEvents.push(value);
  refreshDetails();
}

// ---------- Models ----------
async function loadModels({automatic = false} = {}) {
  if (state.modelsLoading || state.running) return;
  if (!gatewayKey()) {
    if (!automatic) setStatus('请先输入网关密钥。', true);
    return;
  }
  state.modelsLoading = true;
  syncControls();
  setStatus('正在载入模型…');
  try {
    let data;
    try { data = await api('/v1/models?refresh=true'); } catch (error) {
      if (error instanceof StaleError) throw error;
      throw safeFailure('模型载入失败，请检查网关密钥和网站凭证。');
    }
    let capabilities;
    try { capabilities = await api('/v1/capabilities'); } catch (error) {
      if (error instanceof StaleError) throw error;
      throw safeFailure('能力信息载入失败，请升级网关并重试。');
    }
    if (!Array.isArray(capabilities.models)) throw safeFailure('能力信息格式无法识别。');
    if (!Array.isArray(data.data) || !data.data.length) throw safeFailure('网关没有返回可用模型，请检查模型配置。');
    const models = data.data.filter(item => item && typeof item.id === 'string' && item.id.length <= 120);
    if (!models.length) throw safeFailure('模型列表格式无法识别。');
    state.capabilities = capabilities;
    const previous = $('model').value;
    $('model').replaceChildren(...models.map(item => new Option(typeof item.name === 'string' ? item.name.slice(0, 256) : item.id, item.id)));
    if (models.some(item => item.id === previous)) $('model').value = previous;
    refreshOptions(true);
    renderAttachments();
    if (!state.history.length) setEmpty('选择模型后输入第一条消息。');
    setStatus('已载入 ' + models.length + ' 个配置模型；这不表示已完成上游可用性验证。');
  } catch (error) {
    if (!(error instanceof StaleError)) setStatus(error.safeUiMessage || '模型载入失败，请检查网关连接。', true);
  } finally {
    state.modelsLoading = false;
    syncControls();
  }
}

function forgetModels(placeholder) {
  state.capabilities = null;
  $('model').replaceChildren(new Option(placeholder, ''));
}

$('loadModels').addEventListener('click', () => void loadModels());
$('model').addEventListener('change', () => resetChat({selectFirst: false}));

// ---------- Attachments ----------
function attachmentState(file) {
  if (file.model !== $('model').value) return '模型不匹配';
  if (file.expires_at <= Date.now() / 1000) return '已过期';
  return '';
}

function renderAttachments() {
  const model = selectedModel();
  const images = model?.attachments.images, documents = model?.attachments.documents;
  const allowed = [...(images?.supported ? images.mime_types : []), ...(documents?.supported ? documents.extensions : [])];
  $('attachment').accept = allowed.join(',');
  const items = state.pendingAttachments.map(file => {
    const problem = attachmentState(file);
    const remove = element('button', {className: 'icon-button', attrs: {type: 'button', 'aria-label': '移除 ' + file.filename, title: '移除'}});
    remove.innerHTML = icon('x');
    remove.disabled = state.running;
    remove.addEventListener('click', () => removeAttachments([file.id]));
    const parts = [element('span', {text: file.filename})];
    if (problem) parts.push(element('span', {text: problem, attrs: {'data-outcome': 'failed'}}));
    return element('li', {className: problem ? 'bad' : ''}, [...parts, remove]);
  });
  $('attachmentList').replaceChildren(...items);
  const mib = bytes => (bytes / 1024 / 1024).toLocaleString();
  $('attachmentStatus').textContent = model
    ? `图片：${images.supported ? images.mime_types.join(', ') + '，小于 ' + mib(images.max_bytes) + ' MiB' : '未开放'}。`
      + `文档：${documents.supported ? documents.extensions.join(', ') + '，小于 ' + mib(documents.max_bytes) + ' MiB' : '未开放'}。`
      + `有效期 ${state.capabilities.files.ttl_seconds} 秒，重启网关后需重传。`
    : '先载入模型以查看附件格式与限制。';
  refreshDetails();
  syncControls();
}

/** Releases gateway-side references only; the website keeps any uploaded copy. */
function removeAttachments(ids) {
  state.pendingAttachments = state.pendingAttachments.filter(file => !ids.includes(file.id));
  for (const id of ids) {
    try { void gatewayFetch('/v1/files/' + encodeURIComponent(id), {method: 'DELETE'}).catch(() => {}); } catch { /* No key: nothing to release. */ }
  }
  renderAttachments();
}

async function uploadAttachments(files) {
  if (state.running || state.modelsLoading || !files.length) return;
  const model = selectedModel();
  if (!model) {
    setStatus('请先载入模型。', true);
    return;
  }
  for (const file of files) {
    const isImage = model.attachments.images.mime_types.includes(file.type);
    const rule = isImage ? model.attachments.images : model.attachments.documents;
    const suffix = '.' + file.name.split('.').pop().toLowerCase();
    if (!rule.supported || (!isImage && !rule.extensions.includes(suffix)) || !file.size || file.size >= rule.max_bytes) {
      setStatus('附件类型、模型能力或大小不符合当前限制：' + file.name, true);
      $('attachment').value = '';
      return;
    }
  }
  state.controller = new AbortController();
  state.running = true;
  syncControls();
  setStatus('正在上传附件到网站，未发送聊天…');
  try {
    for (const file of files) {
      const form = new FormData();
      form.append('file', file);
      form.append('model', $('model').value);
      form.append('purpose', 'user_data');
      form.append('conversation_id', state.conversation);
      state.conversationUsed = true;
      const response = await gatewayFetch('/v1/files', {method: 'POST', body: form, signal: state.controller.signal});
      const data = await response.json();
      if (!response.ok || data.error) {
        noteEvent(data);
        throw safeFailure('附件上传失败：' + file.name + '。详情见请求与响应详情。');
      }
      if (!state.pendingAttachments.some(item => item.id === data.id)) {
        state.pendingAttachments.push({id: data.id, filename: data.filename, model: data.model, kind: data.kind, expires_at: data.expires_at});
      }
      state.attachmentDetails.push(data);
      renderAttachments();
    }
    setStatus('附件已上传。发送消息时会通过网页 fileIds / imageUrls 引用附件。');
  } catch (error) {
    setStatus(state.controller.signal.aborted ? '已停止等待上传；网站可能已经保留了附件。' : error.safeUiMessage || '附件上传未完成。', true);
  } finally {
    $('attachment').value = '';
    state.controller = null;
    state.running = false;
    renderAttachments();
  }
}

$('attachment').addEventListener('change', () => void uploadAttachments(Array.from($('attachment').files)));
$('clearAttachments').addEventListener('click', () => removeAttachments(state.pendingAttachments.map(file => file.id)));

// ---------- Transcript ----------
function addMessage(role, text) {
  $('empty')?.remove();
  const bodyText = document.createTextNode(text);
  const reasoningText = document.createTextNode('');
  const reasoning = element('div', {className: 'reasoning'}, [element('div', {className: 'reasoning-title', text: '推理内容'}), reasoningText]);
  reasoning.hidden = true;
  const body = element('div', {className: 'message-body'}, [bodyText]);
  const meta = element('div', {className: 'message-meta'});
  const stateLine = element('div', {className: 'message-state'});
  const card = element('article', {className: 'message ' + role}, [
    element('div', {className: 'message-label', text: role === 'user' ? '你' : '助手'}), reasoning, body, meta, stateLine,
  ]);
  $('transcript').append(card);
  scrollMessages();
  return {bodyText, reasoning, reasoningText, meta, state: stateLine};
}

function scrollMessages() {
  $('transcript').scrollTop = $('transcript').scrollHeight;
}

const USAGE_LABELS = {prompt_tokens: '输入', completion_tokens: '输出', total_tokens: '合计'};

function chip(name, value) {
  return element('span', {className: 'chip'}, [document.createTextNode(name + ' '), element('b', {text: String(value)})]);
}

function renderMetadata(target, metadata) {
  const parts = [];
  const chips = [];
  for (const [name, value] of Object.entries(metadata.usage || {})) {
    if (typeof value === 'number') chips.push(chip(USAGE_LABELS[name] || name, value.toLocaleString('zh-CN')));
  }
  const reasoningTokens = metadata.usage?.completion_tokens_details?.reasoning_tokens;
  if (typeof reasoningTokens === 'number') chips.push(chip('推理', reasoningTokens.toLocaleString('zh-CN')));
  const upstream = metadata.upstream_metadata;
  if (upstream && typeof upstream === 'object') {
    for (const [name, value] of Object.entries(upstream)) chips.push(chip('网站 ' + name, typeof value === 'object' ? JSON.stringify(value) : value));
  } else if (upstream !== undefined) chips.push(chip('网站计数', JSON.stringify(upstream)));
  if (chips.length) parts.push(element('div', {className: 'chips'}, chips));

  if (metadata.sources_status) {
    const sources = element('div', {className: 'sources'});
    const links = metadata.sources?.links || [];
    if (metadata.sources_status === 'failed') sources.append(element('span', {text: '搜索来源获取失败（' + (metadata.sources_error || 'unknown') + '）'}));
    else if (!links.length) sources.append(element('span', {text: '本轮未返回搜索来源。'}));
    for (const link of links) {
      try {
        const url = new URL(link.link);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) continue;
        const anchor = element('a', {text: link.title || url.hostname, attrs: {href: url.href, target: '_blank', rel: 'noopener noreferrer'}});
        sources.append(element('span', {}, [anchor, document.createTextNode(link.snippet ? ' ' + link.snippet : '')]));
      } catch { /* Invalid URLs are not linked. */ }
    }
    parts.push(sources);
  }
  target.replaceChildren(...parts);
}

// ---------- Streaming ----------
async function readSse(response, onData) {
  if (!response.body) throw safeFailure('响应没有可读取的流。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', {fatal: true});
  let pending = '', dataLines = [], eventName = 'message', doneSeen = false;

  function dispatch() {
    if (!dataLines.length) {
      eventName = 'message';
      return;
    }
    const data = dataLines.join('\n'), name = eventName;
    dataLines = [];
    eventName = 'message';
    if (data.trim() === '[DONE]') {
      doneSeen = true;
      noteEvent({event: name, data: '[DONE]'});
      return;
    }
    let parsed;
    try { parsed = JSON.parse(data); } catch { throw safeFailure('收到无法识别的兼容流事件，输出未完成。'); }
    noteEvent(parsed);
    if (name === 'error' || parsed?.error) throw safeFailure('网关返回流式错误；详细消息中保留了错误事件。');
    if (doneSeen) throw safeFailure('结束标记后仍有消息，输出未确认完成。');
    onData(parsed);
  }
  function line(value) {
    if (value === '') {
      dispatch();
      return;
    }
    if (value.startsWith(':')) return;
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    let contents = colon < 0 ? '' : value.slice(colon + 1);
    if (contents.startsWith(' ')) contents = contents.slice(1);
    if (field === 'data') dataLines.push(contents);
    else if (field === 'event') eventName = contents;
  }
  function consume(final = false) {
    while (pending.length) {
      const match = /[\r\n]/.exec(pending);
      if (!match) break;
      const index = match.index;
      if (!final && pending[index] === '\r' && index === pending.length - 1) break;
      const width = pending[index] === '\r' && pending[index + 1] === '\n' ? 2 : 1;
      line(pending.slice(0, index));
      pending = pending.slice(index + width);
    }
    if (final && pending) {
      line(pending);
      pending = '';
    }
  }
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      pending += decoder.decode(result.value, {stream: true});
      consume();
      if (doneSeen) {
        await reader.cancel();
        return true;
      }
    }
    pending += decoder.decode();
    consume(true);
    dispatch();
    return doneSeen;
  } finally { reader.releaseLock(); }
}

function buildPayload(userMessage) {
  const payload = {
    model: $('model').value, messages: [...state.history, userMessage], stream: $('stream').checked, conversation_id: state.conversation,
    shanghaitech: {search: $('searchOption').checked, thinking: $('thinkingOption').checked},
  };
  const system = $('systemPrompt').value.trim();
  if (system) payload.messages.unshift({role: 'system', content: system});
  if ($('maxTokens').value) {
    const limit = Number($('maxTokens').value);
    if (!Number.isInteger(limit) || limit < 1 || limit > Number($('maxTokens').max)) throw safeFailure('输出上限超出当前模型允许范围。');
    payload.max_tokens = limit;
  }
  if (payload.stream) payload.stream_options = {include_usage: $('includeUsage').checked};
  let extra;
  try { extra = JSON.parse($('advancedJson').value.trim() || '{}'); } catch { extra = null; }
  if (!extra || Array.isArray(extra) || typeof extra !== 'object' || Object.keys(extra).some(key => !['tools', 'tool_choice', 'store'].includes(key))) {
    throw safeFailure('高级参数须为 JSON 对象，只允许 tools、tool_choice、store。');
  }
  return Object.assign(payload, extra);
}

$('chatForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.running || state.modelsLoading) return;
  if (!selectedModel()) return setStatus('请先载入模型。', true);
  if (!gatewayKey()) return setStatus('请先输入网关密钥。', true);
  const prompt = $('prompt').value.trim();
  if (!prompt) return setStatus('请输入消息。', true);
  if (state.pendingAttachments.some(file => file.model !== $('model').value)) {
    return setStatus('附件已绑定上传时的模型，请切回该模型或新建会话重新上传。', true);
  }
  if (state.pendingAttachments.some(file => file.expires_at <= Date.now() / 1000)) {
    renderAttachments();
    return setStatus('附件引用已过期，请移除后重新上传。', true);
  }
  const userMessage = {
    role: 'user',
    content: state.pendingAttachments.length
      ? [{type: 'text', text: prompt}, ...state.pendingAttachments.map(file => ({type: 'file', file: {file_id: file.id}}))]
      : prompt,
  };
  let payload;
  try { payload = buildPayload(userMessage); } catch (error) { return setStatus(error.safeUiMessage, true); }

  state.currentRequest = payload;
  state.receivedEvents = [];
  state.conversationUsed = true;
  refreshDetails();
  addMessage('user', prompt);
  const assistant = addMessage('assistant', '');
  assistant.state.textContent = '等待响应…';
  $('prompt').value = '';
  state.controller = new AbortController();
  state.running = true;
  syncControls();
  setStatus('请求进行中。真实发送可能消耗网站额度。');

  let content = '', finished = false, finishReason = '', searchProgress = '', metadata = {}, toolCalls = [];
  const readMetadata = data => {
    if (!data?.usage && !data?.shanghaitech) return;
    if (data.usage) metadata.usage = data.usage;
    if (data.shanghaitech) metadata = {...metadata, ...data.shanghaitech};
    renderMetadata(assistant.meta, metadata);
    const progress = data.shanghaitech?.search_progress;
    if (progress) searchProgress = progress.stage === 'searching' ? '正在搜索…' : '搜索完成' + (Number.isInteger(progress.count) ? '，' + progress.count + ' 条' : '');
  };
  const appendContent = text => {
    content += text;
    assistant.bodyText.appendData(text);
  };
  const appendReasoning = text => {
    assistant.reasoningText.appendData(text);
    assistant.reasoning.hidden = !assistant.reasoningText.data;
  };

  try {
    const response = await gatewayFetch('/v1/chat/completions', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal: state.controller.signal,
    });
    if (!response.ok) {
      try {
        const data = await response.json();
        if (data?.error) noteEvent(data);
      } catch { /* No response text is inserted into the UI. */ }
      throw safeFailure('网关拒绝了请求（HTTP ' + response.status + '）。请查看详细错误并检查配置。');
    }
    if (payload.stream) {
      if (!(response.headers.get('content-type') || '').toLowerCase().includes('text/event-stream')) throw safeFailure('网关没有返回预期的流式内容类型。');
      const done = await readSse(response, data => {
        readMetadata(data);
        if (!data || !Array.isArray(data.choices)) throw safeFailure('兼容流消息格式无法识别。');
        for (const choice of data.choices) {
          if (choice.index !== 0 && choice.index !== undefined) continue;
          const delta = choice.delta || {};
          if (typeof delta.content === 'string') appendContent(delta.content);
          if (typeof delta.reasoning_content === 'string') appendReasoning(delta.reasoning_content);
          if (delta.tool_calls) toolCalls.push(...delta.tool_calls);
          if (choice.finish_reason != null) {
            finished = true;
            finishReason = choice.finish_reason;
          }
        }
        assistant.state.textContent = searchProgress || '接收中…';
        scrollMessages();
      });
      if (!done || !finished) throw safeFailure('流已断开，但没有完整结束标记；本次内容不加入后续上下文。');
    } else {
      const data = await response.json();
      noteEvent(data);
      readMetadata(data);
      if (data?.error) throw safeFailure('网关返回错误；本次内容不加入后续上下文。');
      const choice = Array.isArray(data?.choices) ? data.choices[0] : null;
      if (!choice?.message || (typeof choice.message.content !== 'string' && !choice.message.tool_calls) || choice.finish_reason == null) {
        throw safeFailure('完整响应格式无法识别；本次内容不加入后续上下文。');
      }
      appendContent(choice.message.content || '');
      if (typeof choice.message.reasoning_content === 'string') appendReasoning(choice.message.reasoning_content);
      toolCalls = choice.message.tool_calls || [];
      finishReason = choice.finish_reason;
      finished = true;
      scrollMessages();
    }
    if (toolCalls.length) {
      assistant.state.textContent = '已收到工具调用；本页不执行工具，详细响应中可查看。';
      setStatus('工具调用测试完成；本轮未加入聊天上下文。');
      return;
    }
    if (!content.trim()) throw safeFailure('本次已结束但没有文本回答，详细内容仍保留；本次消息不加入后续上下文。');
    const historicalUser = {...userMessage};
    if (Array.isArray(historicalUser.content)) {
      historicalUser.content = historicalUser.content.map(part => {
        const file = state.pendingAttachments.find(item => item.id === part.file?.file_id);
        return file?.kind === 'document' ? {type: 'text', text: '【上传文件：' + file.filename + '】'} : part;
      });
    }
    state.history.push(historicalUser, {role: 'assistant', content});
    state.pendingAttachments = state.pendingAttachments.filter(file => file.kind === 'document');
    assistant.state.textContent = '已完成，已加入后续上下文' + (finishReason === 'length' ? '（达到输出上限）' : '');
    setStatus('已完成。当前历史 ' + state.history.length + ' 条。');
  } catch (error) {
    const cancelled = state.controller?.signal.aborted;
    assistant.state.textContent = cancelled ? '已停止，部分内容未加入后续上下文' : '未完成，本次内容未加入后续上下文';
    assistant.state.classList.add('failed');
    setStatus(cancelled ? '已停止读取。本次消息不加入后续上下文；这不保证上游停止计算或退款。'
      : error.safeUiMessage || '请求未完成，请检查网关连接或详细错误。', !cancelled);
  } finally {
    state.controller = null;
    state.running = false;
    renderAttachments();
    scrollMessages();
    $('prompt').focus();
  }
});

$('stream').addEventListener('change', syncControls);
$('stop').addEventListener('click', () => state.controller?.abort());
$('prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $('chatForm').requestSubmit();
  }
});

// ---------- Conversations ----------
function resetChat({selectFirst = true, focus = true, message = '已清空当前页面的对话。原网站的历史记录不受影响。'} = {}) {
  if (state.running) return;
  if (selectFirst && $('model').options.length) $('model').selectedIndex = 0;
  refreshOptions(true);
  state.history = [];
  state.conversation = crypto.randomUUID();
  state.conversationUsed = false;
  state.pendingAttachments = [];
  state.attachmentDetails = [];
  state.receivedEvents = [];
  state.currentRequest = null;
  $('attachment').value = '';
  setEmpty(state.capabilities ? '新会话已就绪。' : '验证网关密钥后会自动载入模型。');
  renderAttachments();
  if (message) setStatus(message);
  if (focus) $('prompt').focus();
}

$('newChat').addEventListener('click', () => resetChat());
$('deleteChat').addEventListener('click', async () => {
  if (state.running || state.modelsLoading || !state.conversationUsed) return;
  if (!gatewayKey()) return setStatus('请先输入网关密钥。', true);
  if (!window.confirm('删除当前会话及关联的网站历史？删除后不可恢复。')) return;
  state.running = true;
  syncControls();
  setStatus('正在删除会话…');
  try {
    const result = await api('/v1/conversations/' + encodeURIComponent(state.conversation), {method: 'DELETE', timeout: 330000});
    if (result.deleted !== true) throw safeFailure('删除未完成，当前消息已保留。错误：delete_failed');
    state.running = false;
    resetChat({message: '会话已删除，网站确认清理 ' + result.deleted_groups + ' 个会话组。'});
  } catch (error) {
    if (error instanceof StaleError) return;
    setStatus(error.safeUiMessage || (error.code === 'conversation_mapping_missing'
      ? '找不到完整的上游映射。旧会话或登录已变更的会话请到网站删除；当前消息已保留。'
      : error.code ? '删除未完成，当前消息已保留。错误：' + error.code : '无法确认删除结果，当前消息已保留。请检查网关后重试。'), true);
  } finally {
    state.running = false;
    syncControls();
  }
});

// ---------- Shell events ----------
on('verified', () => {
  if (!state.capabilities) void loadModels({automatic: true});
});
on('key-changed', () => {
  state.controller?.abort();
  forgetModels('验证密钥后自动载入');
  resetChat({selectFirst: false, focus: false, message: '发送消息会真实调用所选模型。'});
});
on('credentials-changed', ({reload} = {}) => {
  forgetModels(reload ? '正在重新载入模型' : '导入凭证后重新载入');
  resetChat({selectFirst: false, focus: false, message: ''});
  if (reload) void loadModels({automatic: true});
});
on('flags', syncControls);

renderAttachments();
refreshDetails();
syncControls();
