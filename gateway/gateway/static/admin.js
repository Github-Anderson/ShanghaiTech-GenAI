// Console shell, status page, request / login logs and credential management.
import {$, api, copyText, element, emit, formatNumber, formatTime, icon, on, renderIcons, session, setFlag, StaleError} from './core.js';

renderIcons();

const LABELS = {
  complete: '完成', failed: '失败', cancelled: '断开 / 取消', running: '进行中',
  reachable: '已收到响应', unreachable: '连接失败', timeout: '超时',
  valid: '有效', saved: '已保存', unverified: '未验证',
  credential_import: '导入凭证', credential_logout: '取消登录', credential_check: '凭证检查',
};
const label = value => LABELS[value] || value;
const PAGE_SIZE = 50;
const narrow = matchMedia('(max-width: 700px)');

// ---------- Theme ----------
const THEME_KEY = 'genai-console-theme';
const darkScheme = matchMedia('(prefers-color-scheme: dark)');
let theme = 'system';
try { theme = localStorage.getItem(THEME_KEY) || 'system'; } catch { /* Storage may be blocked. */ }
if (!['system', 'light', 'dark'].includes(theme)) theme = 'system';

function setTheme(choice) {
  theme = choice;
  document.documentElement.dataset.theme = choice === 'system' ? (darkScheme.matches ? 'dark' : 'light') : choice;
  try { localStorage.setItem(THEME_KEY, choice); } catch { /* Storage may be blocked. */ }
  for (const button of document.querySelectorAll('[data-theme-choice]')) {
    button.setAttribute('aria-pressed', String(button.dataset.themeChoice === choice));
  }
}
setTheme(theme);
darkScheme.addEventListener('change', () => { if (theme === 'system') setTheme(theme); });
for (const button of document.querySelectorAll('[data-theme-choice]')) {
  button.addEventListener('click', () => setTheme(button.dataset.themeChoice));
}

// ---------- Header island ----------
// On narrow screens the connection row collapses and only the navigation row stays pinned.
const island = $('island');
function setIslandOpen(open) {
  island.classList.toggle('collapsed', !open);
  $('connectionToggle').setAttribute('aria-expanded', String(open));
  measureIsland();
}
function measureIsland() {
  const offset = narrow.matches ? Math.max(0, document.querySelector('nav').offsetTop - 6) : 0;
  island.style.setProperty('--island-stick', offset + 'px');
}
$('connectionToggle').addEventListener('click', () => setIslandOpen(island.classList.contains('collapsed')));
new ResizeObserver(measureIsland).observe(island);
narrow.addEventListener('change', measureIsland);

function revealKeyInput() {
  setIslandOpen(true);
  window.scrollTo({top: 0});
  $('apiKey').focus();
}
$('focusKey').addEventListener('click', revealKeyInput);

// ---------- Gateway key ----------
const REMEMBER_KEY = 'genai-console-key';
function rememberKey() {
  try {
    if ($('rememberKey').checked && session.verified && $('apiKey').value.trim()) sessionStorage.setItem(REMEMBER_KEY, $('apiKey').value.trim());
    else if (!$('rememberKey').checked || !session.verified) sessionStorage.removeItem(REMEMBER_KEY);
  } catch { /* Storage may be blocked. */ }
}
function restoreKey() {
  try {
    const key = sessionStorage.getItem(REMEMBER_KEY);
    if (!key) return false;
    $('apiKey').value = key;
    $('rememberKey').checked = true;
    return true;
  } catch { return false; }
}
$('rememberKey').addEventListener('change', () => {
  if (!$('rememberKey').checked) {
    try { sessionStorage.removeItem(REMEMBER_KEY); } catch { /* Storage may be blocked. */ }
  } else rememberKey();
});

$('apiKey').addEventListener('input', () => {
  session.revision++;
  status.sequence++;
  logs.sequence++;
  setVerified(false);
  status.connected = false;
  status.data = null;
  status.updated = 0;
  status.lastProbeAttempt = 0;
  status.connectionMessage = status.probeMessage = '';
  credentialMessage('');
  try { sessionStorage.removeItem(REMEMBER_KEY); } catch { /* Storage may be blocked. */ }
  clearLogs('请验证网关密钥后刷新。');
  emit('key-changed');
  render();
});
$('toggleKey').addEventListener('click', () => {
  const visible = $('apiKey').type === 'password';
  const title = visible ? '隐藏密钥' : '显示密钥';
  $('apiKey').type = visible ? 'text' : 'password';
  $('toggleKey').innerHTML = icon(visible ? 'eye-off' : 'eye');
  $('toggleKey').setAttribute('aria-pressed', String(visible));
  $('toggleKey').setAttribute('aria-label', title);
  $('toggleKey').title = title;
});
$('apiKey').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    $('connect').click();
  }
});
$('connect').addEventListener('click', async () => {
  $('connect').disabled = true;
  try {
    await refreshStatus();
    if (status.connected) {
      maybeProbe();
      if (!$('logPage').hidden) await loadLogs();
    }
  } finally { $('connect').disabled = session.busy; }
});

function setVerified(value) {
  if (session.verified === value) return;
  session.verified = value;
  rememberKey();
  if (value) {
    setIslandOpen(false);
    emit('verified');
  }
}

// ---------- Status page ----------
const status = {
  sequence: 0, pending: 0, connected: false, paused: false, range: '24h', data: null, updated: 0, latency: 0,
  probePending: false, lastProbeAttempt: 0, probeMessage: '', connectionMessage: '',
};

function badge(id, text, tone = 'neutral') {
  $(id).textContent = text;
  $(id).className = 'badge ' + tone;
}
function chip(id, text, tone = 'neutral') {
  $(id).classList.toggle('bad', tone === 'bad');
  $(id).classList.toggle('neutral', tone === 'neutral');
  $(id).querySelector('span:last-of-type').textContent = text;
}
function endpointText(result, isImage = false) {
  if (!result) return '待检查';
  if (result.state !== 'reachable') return label(result.state);
  // The image host has no public index: any HTTP response proves reachability, not upload authorization.
  if (isImage) return '可达 · ' + result.duration_ms + ' ms';
  return result.ok ? result.duration_ms + ' ms' : 'HTTP ' + result.http_status;
}

function render() {
  const {data, connected, updated, paused} = status;
  const gateway = data?.gateway, stats = data?.statistics, probe = data?.last_probe, credentials = data?.credentials;
  const fresh = connected && Date.now() - updated < 12000;
  const probeFresh = fresh && probe && data.server_time + (Date.now() - updated) / 1000 - probe.checked_at < 90;
  const auth = probeFresh ? probe.website_authentication : null;

  // Header
  const connectionText = fresh ? '已连接' : data ? '连接待更新' : '未连接';
  const connectionTone = fresh ? 'good' : status.connectionMessage && !data ? 'bad' : 'neutral';
  chip('connectionChip', connectionText, connectionTone);
  $('connectionToggleLabel').textContent = connectionText;
  $('connectionToggle').classList.toggle('neutral', connectionTone === 'neutral');
  $('connectionToggle').classList.toggle('bad', connectionTone === 'bad');
  $('connect').classList.toggle('verified', fresh);

  // Empty state until the first successful status read
  $('statusEmpty').hidden = Boolean(data);
  $('statusContent').hidden = !data;

  badge('gatewayBadge', fresh ? '正常' : data ? '待更新' : '未连接', fresh ? 'good' : 'neutral');
  $('gatewayDetail').textContent = fresh ? '密钥认证通过 · ' + status.latency + ' ms' : data ? '当前展示上次收到的数据' : '请先验证网关密钥';
  $('gatewayRing').innerHTML = icon(fresh ? 'check' : 'info');

  const main = probeFresh ? probe.main_page : null, image = probeFresh ? probe.image_service : null;
  const schoolOK = main?.ok && image?.state === 'reachable';
  badge('schoolBadge', !probeFresh ? '待检查' : schoolOK ? '可达' : '异常', !probeFresh ? 'neutral' : schoolOK ? 'good' : 'bad');
  $('schoolDetail').textContent = probeFresh ? '主站 ' + endpointText(main) + ' · 图片服务 ' + endpointText(image, true)
    : probe ? '上次检查 ' + formatTime(probe.checked_at) : '连接后自动检查学校服务';
  $('schoolDetail').title = image?.http_status ? '图片服务根路径返回 HTTP ' + image.http_status + '，说明服务可达；上传权限需实际上传验证。' : '';
  $('schoolRing').innerHTML = icon(!probeFresh ? 'info' : schoolOK ? 'check' : 'alert');
  $('schoolRing').classList.toggle('bad', Boolean(probeFresh && !schoolOK));

  const authState = !fresh ? '待验证' : credentials?.state === 'missing' ? '未导入' : credentials?.state === 'unreadable' ? '无法读取'
    : auth?.state === 'valid' ? '有效' : auth?.state === 'failed' ? '验证失败' : '待验证';
  const authTone = authState === '有效' ? 'good' : ['验证失败', '无法读取'].includes(authState) ? 'bad' : 'neutral';
  badge('authBadge', authState, authTone);
  chip('credentialChip', '网站凭证' + authState, authTone);
  $('authDetail').textContent = !fresh ? '连接后验证网站凭证'
    : credentials?.state === 'missing' ? '导入网站导出的凭证 JSON'
    : auth?.state === 'valid' ? '模型目录已验证 · ' + Math.max(0, Math.floor(data.server_time - probe.checked_at)) + ' 秒前'
    : auth?.error_code || (credentials?.state === 'unreadable' ? '请重新导入凭证' : '尚未确认凭证有效性');
  $('authDetail').title = '验证模型目录访问权限；图片上传凭证需通过实际上传验证。';
  $('imageDetail').hidden = credentials?.state !== 'saved';
  $('imageDetail').textContent = credentials?.image_upload_verified_at_export
    ? '图片上传凭证：导出时已验证' : '图片上传凭证：导出时未验证，首次上传图片时确认';

  $('runtimeStatus').textContent = gateway ? '网关 v' + gateway.version + (fresh ? ' · 运行正常' : ' · 上次记录') : '等待连接';
  $('updateStatus').textContent = !session.verified ? '输入密钥后连接' : paused ? '自动刷新已暂停'
    : document.hidden ? '后台已暂停' : !connected ? '连接中断，正在重试' : '每 3 秒刷新';
  $('lastUpdated').textContent = updated ? new Date(updated).toLocaleTimeString('zh-CN', {hourCycle: 'h23'}) + ' 更新' : '尚未更新';
  document.body.classList.toggle('paused', paused || !fresh || document.hidden);

  const message = status.connectionMessage || status.probeMessage || (session.verified && !fresh && data ? '状态已过期，当前展示上次收到的数据。' : '');
  $('connectionStatus').hidden = !message;
  $('connectionStatus').textContent = message;

  const available = stats?.available && stats.window === status.range;
  $('successCount').textContent = available ? formatNumber(stats.complete) : '—';
  $('totalCount').textContent = available ? formatNumber(stats.total) : '—';
  $('successRate').textContent = available && stats.total ? stats.success_rate.toFixed(1) + '%' : '—';
  $('successRate').title = available ? '成功 ' + stats.complete + '，失败 ' + stats.failed + '，取消 ' + stats.cancelled : '暂无统计';
  $('latency').textContent = available && stats.average_duration_ms !== null ? (stats.average_duration_ms / 1000).toFixed(2) : '—';
  $('activeCount').textContent = gateway ? formatNumber(gateway.active_requests) : '—';
  $('concurrencyLimit').textContent = gateway ? formatNumber(gateway.max_concurrent_requests) : '—';
  $('queueCount').textContent = gateway ? formatNumber(gateway.queued_requests) : '—';

  const seconds = gateway?.uptime_seconds || 0;
  const units = seconds >= 86400 ? [Math.floor(seconds / 86400), '天', Math.floor(seconds / 3600) % 24, '小时']
    : seconds >= 3600 ? [Math.floor(seconds / 3600), '小时', Math.floor(seconds / 60) % 60, '分钟']
    : [Math.floor(seconds / 60), '分钟', seconds % 60, '秒'];
  ['uptimeMajor', 'uptimeMajorUnit', 'uptimeMinor', 'uptimeMinorUnit'].forEach((id, index) => {
    $(id).textContent = gateway || index % 2 ? units[index] : '—';
  });
  $('statsCoverage').textContent = !stats?.available ? '统计存储不可用，请检查网关私有目录权限。'
    : '仅统计已认证的模型请求；已结束包含成功、失败和取消。' + (!stats.complete_window ? ' 统计始于 ' + formatTime(stats.started_at) + '。' : '');
}

async function refreshStatus() {
  const sequence = ++status.sequence, revision = session.revision, range = status.range, start = performance.now();
  status.pending++;
  try {
    const result = await api('/v1/admin/status?window=' + range, {timeout: 7000});
    if (sequence !== status.sequence || revision !== session.revision || range !== status.range) return;
    status.data = result;
    status.updated = Date.now();
    status.latency = Math.round(performance.now() - start);
    status.connected = true;
    status.connectionMessage = '';
    setVerified(true);
    render();
  } catch (error) {
    if (error instanceof StaleError || sequence !== status.sequence || revision !== session.revision) return;
    status.connected = false;
    if (error.status === 401) {
      setVerified(false);
      setIslandOpen(true);
    }
    status.connectionMessage = error.message;
    render();
  } finally { status.pending--; }
}

async function probe(automatic = false) {
  if (status.probePending || !status.connected) return;
  if (automatic && (session.busy || session.managing || status.data?.gateway.pending_requests > 0)) return;
  status.probePending = true;
  status.lastProbeAttempt = Date.now();
  $('probe').disabled = true;
  const revision = session.revision;
  status.probeMessage = '正在检查学校连接与网站凭证…';
  render();
  try {
    await api('/v1/admin/probe' + (automatic ? '?automatic=true' : ''), {method: 'POST'});
    if (revision === session.revision) status.probeMessage = '';
  } catch (error) {
    if (!(error instanceof StaleError) && revision === session.revision) {
      status.probeMessage = error.status === 409 ? '其他连接检查正在进行，稍后自动更新。' : error.message;
    }
  } finally {
    status.probePending = false;
    $('probe').disabled = false;
    if (revision === session.revision) await refreshStatus();
  }
}

function maybeProbe() {
  const lastCheck = status.data?.last_probe?.checked_at;
  if (Date.now() - status.lastProbeAttempt >= 60000 && (!lastCheck || status.data.server_time - lastCheck >= 60)) void probe(true);
}

$('refreshStatus').addEventListener('click', async () => {
  await refreshStatus();
  if (status.connected) maybeProbe();
});
$('probe').addEventListener('click', () => {
  if (status.connected) void probe();
  else {
    status.connectionMessage = '请先验证网关连接。';
    render();
  }
});
$('pause').addEventListener('click', () => {
  status.paused = !status.paused;
  const title = status.paused ? '恢复自动刷新' : '暂停自动刷新';
  $('pause').innerHTML = icon(status.paused ? 'play' : 'pause');
  $('pause').title = title;
  $('pause').setAttribute('aria-label', title);
  render();
  if (!status.paused && session.verified) void refreshStatus().then(maybeProbe);
});
for (const button of document.querySelectorAll('[data-range]')) {
  button.addEventListener('click', () => {
    status.range = button.dataset.range;
    for (const other of document.querySelectorAll('[data-range]')) other.setAttribute('aria-pressed', String(other === button));
    render();
    if (session.verified) void refreshStatus();
  });
}

async function tick() {
  if (!session.verified || status.paused || document.hidden || status.pending) {
    render();
    return;
  }
  await refreshStatus();
  if (status.connected) maybeProbe();
}
setInterval(tick, 3000);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) render();
  else void tick();
});

// ---------- Request and login logs ----------
const logs = {sequence: 0, view: 'requests', page: 1, category: 'api', outcome: '', model: ''};
const DETAIL_LABELS = {
  request_id: '请求 ID', method: '方法', path: '路径', model: '模型', http_status: 'HTTP 状态', outcome: '结果',
  error_code: '错误码', duration_ms: '耗时', stream: '流式输出', store: '存储', max_tokens: '输出上限',
  max_completion_tokens: '输出上限', messages_count: '消息数', tools_count: '工具数', options: '网站选项',
  include_usage: '返回流式用量', category: '类别', time: '时间',
};
const OPTION_LABELS = {search: '联网搜索', thinking: '深度思考'};

function readLogURL(search) {
  const params = new URLSearchParams(search);
  logs.view = params.get('view') === 'login' ? 'login' : 'requests';
  logs.category = ['api', 'management', 'all'].includes(params.get('category')) ? params.get('category') : 'api';
  if (logs.category === 'all') logs.category = '';
  logs.outcome = ['complete', 'failed', 'cancelled'].includes(params.get('outcome')) ? params.get('outcome') : '';
  logs.model = (params.get('model') || '').slice(0, 120);
  logs.page = Math.max(1, Number.parseInt(params.get('page'), 10) || 1);
}
function logURL() {
  const params = new URLSearchParams();
  if (logs.view === 'login') params.set('view', 'login');
  else {
    if (logs.category !== 'api') params.set('category', logs.category || 'all');
    if (logs.outcome) params.set('outcome', logs.outcome);
    if (logs.model) params.set('model', logs.model);
  }
  if (logs.page > 1) params.set('page', logs.page);
  const query = params.toString();
  return '/log' + (query ? '?' + query : '');
}
function syncLogControls() {
  for (const button of document.querySelectorAll('[data-view]')) button.setAttribute('aria-pressed', String(button.dataset.view === logs.view));
  $('requestFilters').hidden = logs.view !== 'requests';
  $('loginRetention').hidden = logs.view !== 'login';
  $('logCategory').value = logs.category;
  $('logOutcome').value = logs.outcome;
  ensureModelOption(logs.model);
  $('logModel').value = logs.model;
  $('logCaption').textContent = logs.view === 'login' ? '登录历史' : '请求记录';
  setHead(logs.view === 'login' ? ['时间', '事件', '结果', '错误码'] : ['时间', '请求', '结果', '耗时与详情']);
}
function setHead(names) {
  const row = element('tr', {}, names.map(name => element('th', {text: name, attrs: {scope: 'col'}})));
  $('logHead').replaceChildren(row);
}
function ensureModelOption(model) {
  if (model && ![...$('logModel').options].some(option => option.value === model)) $('logModel').append(new Option(model, model));
}
function updateLogFilters(changes) {
  Object.assign(logs, changes);
  history.replaceState({}, '', logURL());
  syncLogControls();
  void loadLogs();
}

function clearLogs(message) {
  $('logRows').replaceChildren();
  $('logCount').textContent = '';
  $('logPrevious').disabled = $('logNext').disabled = true;
  $('logMessage').textContent = message;
}

function cellLines(primary, secondary = '', {outcome, className} = {}) {
  const main = element('span', {text: primary, className});
  if (outcome) main.dataset.outcome = outcome;
  const lines = element('div', {className: 'cell-lines'}, [main]);
  if (secondary) lines.append(element('span', {className: 'cell-secondary', text: secondary}));
  return lines;
}
function addRow(target, cells, className = '') {
  const row = element('tr', {className});
  for (const cell of cells) row.append(cell instanceof Node ? element('td', {}, [cell]) : element('td', {text: cell}));
  target.append(row);
  return row;
}
function detailValue(name, value) {
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (name === 'duration_ms') return formatNumber(value) + ' 毫秒';
  if (name === 'time') return formatTime(value);
  if (name === 'outcome') return label(value);
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([key, item]) => (OPTION_LABELS[key] || key) + '：' + detailValue(key, item)).join('，') || '—';
  }
  return value === null || value === undefined || value === '' ? '—' : String(value);
}
function detailRow(record, id) {
  const list = element('dl', {className: 'detail-list'});
  for (const [name, value] of Object.entries(record)) {
    if (name === 'id' || name === 'category') continue;
    list.append(element('dt', {text: DETAIL_LABELS[name] || name}), element('dd', {text: detailValue(name, value)}));
  }
  const copy = element('button', {className: 'small', text: '复制原始 JSON', attrs: {type: 'button'}});
  copy.addEventListener('click', async () => {
    try {
      await copyText(JSON.stringify(record, null, 2));
      copy.textContent = '已复制';
    } catch { copy.textContent = '复制失败'; }
    setTimeout(() => { copy.textContent = '复制原始 JSON'; }, 1500);
  });
  const cell = element('td', {attrs: {colspan: '4'}}, [list, copy]);
  const row = element('tr', {className: 'detail-row', attrs: {id}}, [cell]);
  row.hidden = true;
  return row;
}
function renderRequestRows(result) {
  const rows = $('logRows');
  for (const active of result.active || []) {
    const elapsed = Math.max(0, (result.server_time || Date.now() / 1000) - active.time);
    const [day, clock] = formatTime(active.time).split(' ');
    addRow(rows, [
      cellLines(day, clock),
      cellLines(active.method + ' ' + (active.path || '…'), active.model || '', {className: 'cell-path'}),
      cellLines(label('running'), '', {outcome: 'running'}),
      cellLines('已进行 ' + elapsed.toFixed(1) + ' 秒'),
    ], 'active-row');
  }
  for (const record of result.items) {
    const [day, clock] = formatTime(record.time).split(' ');
    const detailId = 'log-detail-' + record.id;
    const toggle = element('button', {className: 'detail-toggle', attrs: {type: 'button', 'aria-expanded': 'false', 'aria-controls': detailId}});
    toggle.innerHTML = '详情' + icon('chevron-down');
    const duration = element('div', {className: 'cell-duration'}, [element('span', {text: formatNumber(record.duration_ms) + ' 毫秒'}), toggle]);
    addRow(rows, [
      cellLines(day, clock),
      cellLines(record.method + ' ' + record.path, record.model || '', {className: 'cell-path'}),
      cellLines(label(record.outcome) + '（' + (record.http_status || '—') + '）', record.error_code || '', {outcome: record.outcome}),
      duration,
    ]);
    const detail = detailRow(record, detailId);
    rows.append(detail);
    toggle.addEventListener('click', () => {
      detail.hidden = !detail.hidden;
      toggle.setAttribute('aria-expanded', String(!detail.hidden));
    });
  }
}
function renderLoginRows(result) {
  for (const record of result.items) {
    const [day, clock] = formatTime(record.time).split(' ');
    addRow($('logRows'), [
      cellLines(day, clock),
      cellLines(label(record.event)),
      cellLines(label(record.result), '', {outcome: record.result}),
      cellLines(record.error_code || '—'),
    ]);
  }
}

async function loadLogs() {
  if (!session.verified && !$('apiKey').value.trim()) {
    clearLogs('请先验证网关密钥。');
    return;
  }
  const sequence = ++logs.sequence, view = logs.view;
  const params = new URLSearchParams({offset: (logs.page - 1) * PAGE_SIZE, limit: PAGE_SIZE});
  if (view === 'requests') {
    params.set('category', logs.category);
    params.set('outcome', logs.outcome);
    params.set('model', logs.model);
  }
  $('logMessage').textContent = '正在载入…';
  try {
    const result = await api((view === 'login' ? '/v1/admin/login-history?' : '/v1/admin/logs?') + params);
    if (sequence !== logs.sequence) return;
    const pages = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
    if (logs.page > pages) {
      logs.page = pages;
      history.replaceState({}, '', logURL());
      void loadLogs();
      return;
    }
    $('logRows').replaceChildren();
    if (view === 'requests') {
      const models = result.models || [];
      const current = logs.model;
      $('logModel').replaceChildren(new Option('全部模型', ''), ...models.map(model => new Option(model, model)));
      ensureModelOption(current);
      $('logModel').value = current;
      renderRequestRows(result);
    } else renderLoginRows(result);
    if (!$('logRows').children.length) {
      $('logRows').append(element('tr', {}, [element('td', {className: 'empty-row', text: '没有符合条件的记录', attrs: {colspan: '4'}})]));
    }
    $('logMessage').textContent = !result.available ? '记录存储不可用，请检查私有目录权限。'
      : view === 'requests' ? 'HTTP 状态与流式最终结果分别记录。' : '';
    $('logCount').textContent = `共 ${formatNumber(result.total)} 条 · 第 ${logs.page} / ${pages} 页`;
    $('logPrevious').disabled = logs.page <= 1;
    $('logNext').disabled = logs.page >= pages;
  } catch (error) {
    if (!(error instanceof StaleError) && sequence === logs.sequence) $('logMessage').textContent = error.message;
  }
}

for (const button of document.querySelectorAll('[data-view]')) {
  button.addEventListener('click', () => updateLogFilters({view: button.dataset.view, page: 1}));
}
$('logCategory').addEventListener('change', () => updateLogFilters({category: $('logCategory').value, page: 1}));
$('logOutcome').addEventListener('change', () => updateLogFilters({outcome: $('logOutcome').value, page: 1}));
$('logModel').addEventListener('change', () => updateLogFilters({model: $('logModel').value, page: 1}));
$('logPrevious').addEventListener('click', () => updateLogFilters({page: Math.max(1, logs.page - 1)}));
$('logNext').addEventListener('click', () => updateLogFilters({page: logs.page + 1}));
$('refreshLogs').addEventListener('click', () => void loadLogs());

// ---------- Navigation ----------
const TITLES = {status: '网关状态', log: '请求日志', test: '模型测试'};
function navigate(url, push = false) {
  const target = new URL(url, location.origin);
  const page = target.pathname === '/log' ? 'log' : target.pathname === '/test' ? 'test' : 'status';
  for (const name of Object.keys(TITLES)) $(name + 'Page').hidden = page !== name;
  for (const link of document.querySelectorAll('nav a')) {
    if (link.dataset.page === page) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  // A bare /log keeps the filters chosen earlier in this tab.
  if (page === 'log' && target.search) readLogURL(target.search);
  const path = page === 'log' ? logURL() : '/' + page;
  if (push) history.pushState({}, '', path);
  else history.replaceState({}, '', path);
  document.title = TITLES[page] + ' · ShanghaiTech GenAI';
  if (page === 'log') {
    syncLogControls();
    if (session.verified) void loadLogs();
  }
  if (page === 'status' && session.verified) void refreshStatus();
}
function interceptLink(event) {
  const link = event.target.closest('a[href^="/"]');
  if (!link || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(link.getAttribute('href'), true);
  window.scrollTo({top: 0});
}
document.querySelector('nav').addEventListener('click', interceptLink);
document.querySelector('.brand').addEventListener('click', interceptLink);
$('loginHistoryLink').addEventListener('click', interceptLink);
window.addEventListener('popstate', () => navigate(location.pathname + location.search));

// ---------- Credentials ----------
function credentialMessage(message, isError = false) {
  $('credentialsStatus').textContent = message;
  $('credentialsStatus').hidden = !message;
  $('credentialsStatus').classList.toggle('error', isError);
}
$('importCredentials').addEventListener('click', () => {
  if (session.busy || session.managing) return;
  if (!$('apiKey').value.trim()) {
    credentialMessage('请先输入网关密钥。', true);
    return;
  }
  $('credentialsFile').click();
});
$('credentialsFile').addEventListener('change', async () => {
  const file = $('credentialsFile').files[0];
  if (!file || session.busy || session.managing) return;
  setFlag('managing', true);
  try {
    if (!file.size || file.size > 65536) throw new Error('请选择不超过 64 KiB 的凭证 JSON。');
    await api('/v1/credentials/import', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: file});
    emit('credentials-changed', {reload: true});
    credentialMessage('凭证已保存，正在验证…');
    status.lastProbeAttempt = 0;
    await refreshStatus();
    await probe();
    credentialMessage('凭证已保存，验证结果见网站凭证状态。');
  } catch (error) {
    if (!(error instanceof StaleError)) credentialMessage(error.message, true);
  } finally {
    $('credentialsFile').value = '';
    setFlag('managing', false);
  }
});
$('logoutCredentials').addEventListener('click', async () => {
  if (session.busy || session.managing) return;
  if (!$('apiKey').value.trim()) {
    credentialMessage('请先输入网关密钥。', true);
    return;
  }
  if (!window.confirm('取消登录会清除本网关保存的主站和图片凭证，并清空测试页的当前会话；原网站登录不受影响。继续？')) return;
  setFlag('managing', true);
  try {
    await api('/v1/credentials/logout', {method: 'POST'});
    emit('credentials-changed', {reload: false});
    credentialMessage('已取消本网关登录，保存的凭证已清除。');
    await refreshStatus();
  } catch (error) {
    if (!(error instanceof StaleError)) {
      credentialMessage(error.status === 409 ? '请等待正在进行的聊天结束后再取消登录。' : error.message, true);
    }
  } finally { setFlag('managing', false); }
});

// ---------- Shared control state ----------
function syncFlags() {
  const locked = session.busy || session.managing;
  $('importCredentials').disabled = locked;
  $('logoutCredentials').disabled = locked;
  $('apiKey').disabled = session.busy;
  $('connect').disabled = session.busy;
}
on('flags', syncFlags);

// ---------- Start ----------
navigate(location.pathname + location.search);
syncFlags();
render();
if (restoreKey()) $('connect').click();
else setIslandOpen(true);
