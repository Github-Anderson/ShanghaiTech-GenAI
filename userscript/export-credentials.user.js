// ==UserScript==
// @name         ShanghaiTech GenAI 凭证导出
// @namespace    local.shanghaitech.gateway
// @version      1.0.1
// @description  导出主站、图片站及联网搜索签名凭证；仅本地下载。
// @match        https://genai.shanghaitech.edu.cn/*
// @run-at       document-start
// @grant        none
// @sandbox      raw
// @noframes
// ==/UserScript==

(() => {
  'use strict';
  if (window.top !== window.self || window.__shtechCredentialExporter) return;
  window.__shtechCredentialExporter = true;
  const MAIN = 'https://genai.shanghaitech.edu.cn';
  const IMAGE = 'https://genaipic.shanghaitech.edu.cn';
  const TIMEOUT = 20000;
  const sourceFetch = window.fetch.bind(window);
  const requests = new WeakMap();
  let active = null;
  let button;
  let status;

  function endpoint(input) {
    try { return new URL(input, location.href); } catch { return null; }
  }
  function isImage(url) {
    return url && url.origin === IMAGE && /^\/{1,2}sys\/common\/upload$/.test(url.pathname);
  }
  function valid(value) {
    return typeof value === 'string' && value.length > 0 && value.length <= 16384 && /^[\x21-\x7e]+$/.test(value);
  }
  function fail(message) {
    if (active) active.reject(new Error(message));
  }
  function complete(job) {
    if (active === job && job.main && job.image && job.uploadFinished) job.resolve();
  }
  function finishUpload(job, verified, diagnostic) {
    if (active !== job || job.uploadFinished) return;
    job.uploadFinished = true;
    job.imageVerified = verified;
    job.diagnostic = diagnostic;
    if (!job.image) {
      fail(`未读取到图片 Token（${diagnostic}），请先在网页正常上传图片后重试。`);
      return;
    }
    complete(job);
  }
  function readLiteralImageToken(method) {
    // The inspected site's upload method supplies a literal token in its
    // header object. Read the CURRENT page's method; never execute its source,
    // search unrelated storage, or embed a captured credential in this script.
    const source = Function.prototype.toString.call(method);
    const values = [...source.matchAll(/(?:^|[,{])\s*["']?token["']?\s*:\s*(["'])([^"'\\\s]+)\1\s*(?=[,}])/g)]
      .map(match => match[2]).filter(value => valid(value) && value.length >= 8);
    const unique = [...new Set(values)];
    return unique.length === 1 ? unique[0] : null;
  }
  function inspect(meta) {
    const job = active;
    if (!job || !meta.url) return;
    const headers = meta.headers;
    if (meta.url.origin === MAIN && meta.url.pathname.startsWith('/htk/')) {
      const token = headers['x-access-token'];
      const tenant = headers['tenant-id'];
      if (valid(token) && valid(tenant)) {
        if (token !== job.expectedToken) {
          fail('登录用户或 Token 已变化，请重新点击导出。');
          return;
        }
        job.main = { 'X-Access-Token': token, 'tenant-id': tenant };
        complete(job);
      }
    }
    // Only capture this job's unique probe, never a concurrent real upload.
    if (isImage(meta.url) && meta.probe) {
      const token = headers.token;
      job.uploadStarted = true;
      if (valid(token)) {
        job.image = { token };
        job.imageSource = 'request_header';
      }
    }
  }
  function isProbe(body) {
    try { return body instanceof FormData && body.get('file')?.name === active?.filename; }
    catch { return false; }
  }

  // Observe only the two allowed origins. No request bodies or responses are logged.
  const proto = XMLHttpRequest.prototype;
  const originalOpen = proto.open;
  const originalHeader = proto.setRequestHeader;
  const originalSend = proto.send;
  proto.open = function (method, url, ...rest) {
    requests.set(this, { url: endpoint(url), headers: Object.create(null) });
    return originalOpen.call(this, method, url, ...rest);
  };
  proto.setRequestHeader = function (name, value) {
    const result = originalHeader.call(this, name, value);
    const meta = requests.get(this);
    const key = String(name).toLowerCase();
    if (active && meta && (meta.url?.origin === MAIN || isImage(meta.url)) &&
        ['x-access-token', 'tenant-id', 'token'].includes(key)) meta.headers[key] = String(value);
    return result;
  };
  proto.send = function (body) {
    const meta = requests.get(this);
    if (meta && active) {
      meta.probe = isProbe(body);
      if (meta.probe && !isImage(meta.url)) {
        fail('网站上传地址已变化，已阻止测试请求。');
        throw new Error('Credential probe destination rejected');
      }
      inspect(meta);
      if (meta.probe) {
        const job = active;
        job.xhr = this;
        this.timeout = TIMEOUT;
        this.addEventListener('load', () => {
          if (active !== job) return;
          try {
            const finalURL = endpoint(this.responseURL);
            const data = this.responseType === 'json' ? this.response : JSON.parse(this.responseText);
            if (!isImage(finalURL)) {
              fail('网站上传响应地址已变化；未导出文件。');
              return;
            }
            if (this.status < 200 || this.status >= 300) {
              finishUpload(job, false, `http_${this.status}`);
            } else if (data?.success !== true) {
              finishUpload(job, false, 'server_rejected');
            } else if (job.imageSource !== 'request_header') {
              finishUpload(job, false, 'request_token_not_observed');
            } else finishUpload(job, true, 'verified');
          } catch { finishUpload(job, false, `unexpected_response_http_${this.status}`); }
        }, { once: true });
        for (const event of ['error', 'abort', 'timeout']) {
          this.addEventListener(event, () => {
            finishUpload(job, false, `xhr_${event}`);
          }, { once: true });
        }
      }
    }
    return originalSend.call(this, body);
  };
  if (typeof window.fetch === 'function') {
    const originalFetch = window.fetch;
    window.fetch = async function (input, init) {
      const url = endpoint(input instanceof Request ? input.url : input);
      const headers = Object.fromEntries(new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined)));
      const probe = isProbe(init?.body);
      if (probe) {
        // The verified site uses Axios/XHR. Stop rather than silently exporting
        // an unverified result if its upload transport changes.
        fail('网站上传已改用 fetch；当前版本需适配，已阻止测试请求。');
        throw new Error('Unsupported credential probe transport');
      }
      inspect({ url, headers });
      return originalFetch.call(this, input, init);
    };
  }

  function findChat() {
    const seen = new Set();
    const queue = [];
    for (const element of document.querySelectorAll('*')) {
      if (element.__vue__) queue.push(element.__vue__);
    }
    for (let i = 0; i < queue.length && i < 15000; i++) {
      const vm = queue[i];
      if (!vm || seen.has(vm)) continue;
      seen.add(vm);
      const methods = vm.$options?.methods;
      if (typeof vm.accessToken === 'function' && typeof methods?.uploadImageFile === 'function' &&
          typeof methods?.getDetail === 'function') return vm;
      queue.push(...(vm.$children || []));
    }
    throw new Error('未找到聊天组件，请登录并打开聊天页面后再试。');
  }
  function tinyPNG(filename) {
    // Public, synthetic pixel; no local files or user content are read.
    const bytes = atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=');
    return new File([Uint8Array.from(bytes, c => c.charCodeAt(0))], filename, { type: 'image/png' });
  }
  function download(data) {
    const blob = new Blob([JSON.stringify(data, null, 2) + '\n'], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `shanghaitech-credentials-${Date.now()}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  function extractSigningSalt(source) {
    // Structure confirmed against app.621d318c.js and app.18e5e173.js.
    // Minification renames the digest import (i -> o); its name is not part
    // of the signing contract. Read a literal only; never evaluate downloaded code.
    // Refuse changed/minified structures instead of guessing another secret.
    const pattern = /var ([A-Za-z_$][\w$]*)="([^"\\\r\n]+)",[A-Za-z_$][\w$]*=function\(\)\{function [\s\S]{0,700}?key:"getSign",value:function\(e,t\)\{var n=this\.parseQueryString\(e\),r=this\.mergeObject\(n,t\),a=this\.sortAsc\(r\);return [A-Za-z_$][\w$]*\(\)\(JSON\.stringify\(a\)\+\1\)\.toUpperCase\(\)\}/g;
    return [...source.matchAll(pattern)].map(match => match[2]);
  }
  async function readSigningSalt() {
    const scripts = [...new Set([...document.scripts].map(script => endpoint(script.src))
      .filter(url => url?.origin === MAIN && /^\/js\/app\.[a-zA-Z0-9]+\.js$/.test(url.pathname))
      .map(url => url.href))];
    if (scripts.length !== 1) throw new Error('联网搜索签名脚本位置无法确认，未导出；请提供当前前端 JS。');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), TIMEOUT);
    try {
      const response = await sourceFetch(scripts[0], { credentials: 'same-origin', cache: 'force-cache',
        redirect: 'error', signal: controller.signal });
      if (!response.ok || endpoint(response.url)?.origin !== MAIN) throw new Error();
      const salts = extractSigningSalt(await response.text());
      if (salts.length !== 1 || !valid(salts[0]) || salts[0].length > 256) {
        throw new Error('联网搜索签名实现与已确认结构不一致，未导出；请提供当前前端 JS。');
      }
      return salts[0];
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('联网搜索')) throw error;
      throw new Error('联网搜索签名脚本读取失败，未导出，请刷新后重试。');
    } finally { clearTimeout(timeout); }
  }
  async function exportCredentials() {
    if (active) return;
    button.disabled = true;
    let job;
    let timer;
    try {
      const vm = findChat();
      const expectedToken = vm.accessToken();
      if (!valid(expectedToken)) throw new Error('未读取到登录 Token，请先登录。');
      if (!isImage(endpoint(vm.uploadAction3))) throw new Error('图片上传地址尚未就绪或已变化，请刷新聊天页。');
      status.textContent = '正在从当前页面的前端 JS 读取联网搜索签名盐…';
      const salt = await readSigningSalt();
      status.textContent = '正在读取主站请求头并上传 1×1 测试 PNG…';
      let resolve;
      let reject;
      const done = new Promise((ok, no) => { resolve = ok; reject = no; });
      job = { expectedToken, filename: `credential-probe-${crypto.randomUUID()}.png`, resolve, reject };
      const literal = readLiteralImageToken(vm.$options.methods.uploadImageFile);
      if (literal) {
        job.image = { token: literal };
        job.imageSource = 'page_upload_method';
      }
      active = job;
      timer = setTimeout(() => {
        if (!job.main) fail('未读取到主站完整请求头，请刷新页面并重新登录后再试。');
        else finishUpload(job, false, job.uploadStarted ? 'upload_timeout' : 'before_request_timeout');
      }, TIMEOUT);
      // Reuse a read-only site GET and its authentic signing/interceptors.
      // Detached contexts keep both probe operations out of chat UI state.
      try {
        const detail = vm.$options.methods.getDetail.call({ url: { detail: vm.url.detail } });
        if (detail?.catch) detail.catch(() => fail('主站配置请求失败，请重新登录后再试。'));
        const context = {
          uploadAction3: vm.uploadAction3, uploadAction2: vm.uploadAction2,
          fileurls: [], activateImageAttachment() {}, startUploadTask() {}, finishUploadTask() {},
          $message: { error() {} }
        };
        const upload = vm.$options.methods.uploadImageFile.call(context, tinyPNG(job.filename), () => {}, () => {});
        Promise.resolve(upload).catch(error => {
          // Do not expose Axios errors: they can contain credential headers.
          const httpStatus = error?.response?.status;
          const code = Number.isInteger(httpStatus) && httpStatus >= 100 && httpStatus <= 599 ? `http_${httpStatus}` :
            job.uploadStarted && error?.code === 'ERR_NETWORK' ? 'network_or_cors' :
            job.uploadStarted ? 'site_upload_rejected' :
            error?.target?.constructor?.name === 'FileReader' ? 'file_read_failed' :
            error?.target?.tagName === 'IMG' ? 'image_decode_failed' : 'before_request_failed';
          finishUpload(job, false, code);
        });
      } catch { finishUpload(job, false, 'site_method_incompatible'); }
      await done;
      if (vm.accessToken() !== expectedToken) throw new Error('登录凭证已变化，请重新导出。');
      download({
        schema: 'shanghaitech-browser-credentials/v1', exported_at: new Date().toISOString(),
        main: { origin: MAIN, headers: job.main },
        signing: { salt },
        image: {
          origin: IMAGE, headers: job.image, token_source: job.imageSource,
          upload_verified: job.imageVerified, upload_diagnostic: job.diagnostic
        },
        note: '包含敏感凭证及联网搜索签名盐。主站与搜索有效性未单独验证；图片凭证有效性以 upload_verified 为准。'
      });
      status.textContent = job.imageVerified
        ? '已发起 JSON 下载，含联网搜索签名盐；图片上传验证成功。未单独发起搜索验证。'
        : `已发起 JSON 下载；图片 Token 未验证（${job.diagnostic}）。已导出取得的值，不代表图片服务认证成功。`;
    } catch (error) {
      // Only our fixed messages reach the UI, never upstream exception contents.
      status.textContent = error instanceof Error && /^(联网搜索|未找到|未读取|图片上传地址|图片上传未|上传响应|图片请求|网站上传|登录|获取凭证|主站配置|测试图片|网页上传)/.test(error.message)
        ? error.message : '导出失败，请刷新页面后重试。';
    } finally {
      clearTimeout(timer);
      active = null;
      if (job?.xhr && job.xhr.readyState !== 4) job.xhr.abort();
      button.disabled = false;
    }
  }
  function mount() {
    if (!document.body) return;
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647';
    const root = host.attachShadow({ mode: 'closed' });
    const box = document.createElement('div');
    box.style.cssText = 'width:265px;padding:12px;background:#fff;color:#17233d;border:1px solid #ccc;border-radius:10px;box-shadow:0 3px 16px #0003;font:13px/1.6 sans-serif';
    button = document.createElement('button');
    button.textContent = '导出凭证（含联网搜索）';
    button.style.cssText = 'padding:7px 10px;cursor:pointer';
    button.addEventListener('click', exportCredentials);
    status = document.createElement('div');
    status.textContent = '点击会上传一张 1×1 PNG，服务器可能保留文件；不发送聊天。JSON 仅下载到本机。';
    box.append(button, status);
    root.append(box);
    document.body.append(host);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
