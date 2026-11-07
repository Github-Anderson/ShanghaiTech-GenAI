// Shared console state, gateway requests and small DOM helpers.
export const $ = id => document.getElementById(id);

const ICONS = {
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  'eye-off': '<path d="m3 3 18 18M10.6 5.1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-3.2 4.1M6.2 6.2A21 21 0 0 0 2 12s3.5 7 10 7a12 12 0 0 0 5.8-1.8M10 10a3 3 0 0 0 4 4"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  list: '<path d="M9 6h12M9 12h12M9 18h12M3 6h.01M3 12h.01M3 18h.01"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z"/>',
  monitor: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  moon: '<path d="M20.9 13a9 9 0 1 1-9.9-9.9A7 7 0 0 0 20.9 13Z"/>',
  key: '<circle cx="8" cy="9" r="4"/><path d="m11 12 9 9m-3-3 3-3m-6 0 3-3"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  play: '<path d="m7 4 14 8-14 8Z"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.2 6.2A8 8 0 0 1 20 12M4 12a8 8 0 0 0 13.8 5.8"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M15 6.5h3M15 17.5h3"/>',
  globe: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  'arrow-right': '<path d="M4 12h16m-5-5 5 5-5 5"/>',
  'chevron-left': '<path d="m15 6-6 6 6 6"/>',
  'chevron-right': '<path d="m9 6 6 6-6 6"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  alert: '<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3h.01"/>',
};

export function icon(name) {
  return '<svg class="icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    + `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.info}</svg>`;
}

export function renderIcons(root = document) {
  for (const element of root.querySelectorAll('[data-icon]')) element.innerHTML = icon(element.dataset.icon);
}

export function element(tag, {className, text, attrs} = {}, children = []) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
  node.append(...children);
  return node;
}

export const formatNumber = value => Number(value).toLocaleString('zh-CN');

export function formatTime(seconds) {
  if (!seconds) return '—';
  return new Date(seconds * 1000).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).replaceAll('/', '-');
}

// Event bus between the shell (admin.js) and the model test page (test.js).
const listeners = new Map();
export function on(name, handler) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(handler);
}
export function emit(name, detail) {
  for (const handler of listeners.get(name) || []) handler(detail);
}

// busy: the test page is sending, uploading or loading models; managing: credentials are being imported or removed.
export const session = {revision: 0, verified: false, busy: false, managing: false};
export function setFlag(name, value) {
  if (session[name] === value) return;
  session[name] = value;
  emit('flags');
}

export const gatewayKey = () => $('apiKey').value.trim();

export class StaleError extends Error {}

export class GatewayRequestError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** Raw authorized fetch; callers handle the response body. */
export function gatewayFetch(path, options = {}) {
  const key = gatewayKey();
  if (!key) throw new GatewayRequestError('请先输入网关密钥。', 0);
  return fetch(path, {
    ...options,
    headers: {Authorization: 'Bearer ' + key, ...options.headers},
    credentials: 'omit',
    cache: 'no-store',
  });
}

/** JSON request; results that arrive after the key changed raise StaleError. */
export async function api(path, {timeout = 40000, ...options} = {}) {
  const key = gatewayKey(), revision = session.revision;
  let response;
  try {
    response = await gatewayFetch(path, {...options, signal: options.signal || AbortSignal.timeout(timeout)});
  } catch (error) {
    if (revision !== session.revision) throw new StaleError();
    if (error instanceof GatewayRequestError || options.signal?.aborted) throw error;
    throw new GatewayRequestError('无法连接网关，请检查服务后重试。', 0);
  }
  const result = await response.json().catch(() => null);
  if (revision !== session.revision || key !== gatewayKey()) throw new StaleError();
  if (!response.ok || !result) {
    const code = result?.error?.code;
    const message = response.status === 401 ? '网关密钥无效，请重新验证。'
      : '操作未完成' + (code ? '（' + code + '）' : '（HTTP ' + response.status + '）') + '。';
    throw new GatewayRequestError(message, response.status, code);
  }
  return result;
}

/** Clipboard writes need a secure context; plain-HTTP remote gateways fall back to a selection copy. */
export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = element('textarea', {attrs: {readonly: '', 'aria-hidden': 'true'}});
  area.value = text;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.append(area);
  area.select();
  const copied = document.execCommand('copy');
  area.remove();
  if (!copied) throw new Error('copy failed');
}
