(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const paths = {
    eye:'<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>', 'eye-off':'<path d="m3 3 18 18M10.6 5.1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-3.2 4.1M6.2 6.2A21 21 0 0 0 2 12s3.5 7 10 7a12 12 0 0 0 5.8-1.8M10 10a3 3 0 0 0 4 4"/>',
    activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>', list:'<path d="M9 6h12M9 12h12M9 18h12M3 6h.01M3 12h.01M3 18h.01"/>',
    chat:'<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z"/>',
    monitor:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>', sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>', moon:'<path d="M20.9 13a9 9 0 1 1-9.9-9.9A7 7 0 0 0 20.9 13Z"/>',
    key:'<circle cx="8" cy="9" r="4"/><path d="m11 12 9 9m-3-3 3-3m-6 0 3-3"/>', upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>', more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>', pause:'<path d="M8 5v14M16 5v14"/>', play:'<path d="m7 4 14 8-14 8Z"/>', refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.2 6.2A8 8 0 0 1 20 12M4 12a8 8 0 0 0 13.8 5.8"/>',
    'check-circle':'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', layers:'<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>', server:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M15 6.5h3M15 17.5h3"/>', globe:'<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>', shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>', check:'<path d="m5 12 4 4L19 6"/>', chart:'<path d="M4 20h16M7 16V9m5 7V4m5 12v-5"/>', 'arrow-right':'<path d="M4 12h16m-5-5 5 5-5 5"/>', 'arrow-up-right':'<path d="M6 18 18 6M6 6h12v12"/>', 'arrow-up':'<path d="M12 20V4m-6 6 6-6 6 6"/>', 'chevron-left':'<path d="m15 6-6 6 6 6"/>', 'chevron-right':'<path d="m9 6 6 6-6 6"/>', plus:'<path d="M12 4v16M4 12h16"/>', sliders:'<path d="M4 7h7m5 0h4M4 17h3m5 0h8"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>', info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>', stop:'<rect x="6" y="6" width="12" height="12" rx="1"/>', x:'<path d="m6 6 12 12M6 18 18 6"/>', sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4"/>', alert:'<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3h.01"/>'
  };
  const icon = name => `<svg class="icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
  document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
  const ui = window.gatewayUI = {busy: false, managing: false};
  const fmt = n => Number(n).toLocaleString('zh-CN');
  const date = value => value ? new Date(value * 1000).toLocaleString('zh-CN', {year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'}).replaceAll('/', '-') : '—';
  const labels = {complete:'完成', failed:'失败', cancelled:'断开 / 取消', reachable:'已收到响应', unreachable:'连接失败', timeout:'超时'};
  const label = value => labels[value] || value;
  let revision = 0, statusSequence = 0, statusPending = 0, logSequence = 0, logOffset = 0;
  let verified = false, connected = false, paused = false, range = '24h', data = null, updated = 0, latency = 0;
  let probePending = false, lastProbeAttempt = 0, probeMessage = '', connectionMessage = '';
  let theme = 'system';
  try { theme = localStorage.getItem('genai-console-theme') || 'system'; } catch {}
  if (!['system','light','dark'].includes(theme)) theme = 'system';
  const media = matchMedia('(prefers-color-scheme: dark)');
  function setTheme(choice) {
    theme = choice;
    document.documentElement.dataset.theme = choice === 'system' ? (media.matches ? 'dark' : 'light') : choice;
    try { localStorage.setItem('genai-console-theme', choice); } catch {}
    document.querySelectorAll('[data-theme-choice]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.themeChoice === choice)));
  }
  setTheme(theme);
  media.addEventListener('change', () => { if (theme === 'system') setTheme(theme); });
  document.querySelectorAll('[data-theme-choice]').forEach(b => b.addEventListener('click', () => setTheme(b.dataset.themeChoice)));
  async function api(path, options = {}) {
    const key = $('apiKey').value.trim(), version = revision;
    if (!key) throw new Error('请先输入网关密钥。');
    let response;
    try {
      response = await fetch(path, {...options, headers:{Authorization:'Bearer ' + key, ...options.headers}, credentials:'omit', cache:'no-store', signal:AbortSignal.timeout(path.startsWith('/v1/admin/status') ? 7000 : 40000)});
    } catch {
      if (version !== revision) throw Object.assign(new Error(), {stale:true});
      throw new Error('无法连接网关，请检查服务后重试。');
    }
    const result = await response.json().catch(() => null);
    if (version !== revision || key !== $('apiKey').value.trim()) throw Object.assign(new Error(), {stale:true});
    if (!response.ok || !result) {
      const error = new Error(response.status === 401 ? '网关密钥无效，请重新验证。' : '操作未完成' + (result?.error?.code ? '（' + result.error.code + '）' : '（HTTP ' + response.status + '）') + '。');
      error.status = response.status;
      throw error;
    }
    return result;
  }
  function badge(id, text, tone = 'neutral') { $(id).textContent = text; $(id).className = 'badge ' + tone; }
  function chip(id, text, tone = 'neutral') {
    $(id).classList.toggle('bad', tone === 'bad'); $(id).classList.toggle('neutral', tone === 'neutral');
    $(id).lastElementChild.textContent = text;
  }
  function endpoint(result) {
    if (!result) return '待检查';
    return result.state === 'reachable' ? (result.ok ? result.duration_ms + ' ms' : 'HTTP ' + result.http_status) : label(result.state);
  }
  function render() {
    const g = data?.gateway, stats = data?.statistics;
    const fresh = connected && Date.now() - updated < 12000;
    const p = data?.last_probe;
    const probeFresh = fresh && p && data.server_time + (Date.now() - updated) / 1000 - p.checked_at < 90;
    const auth = probeFresh ? p.website_authentication : null;
    const credentials = data?.credentials;
    chip('connectionChip', fresh ? '已连接' : data ? '连接待更新' : '未连接', fresh ? 'good' : 'neutral');
    $('connect').classList.toggle('verified', fresh);
    badge('gatewayBadge', fresh ? '正常' : data ? '待更新' : '未连接', fresh ? 'good' : 'neutral');
    $('gatewayDetail').textContent = fresh ? '密钥认证通过 · ' + latency + ' ms' : data ? '当前展示上次收到的数据' : '请先验证网关密钥';
    $('gatewayRing').innerHTML = icon(fresh ? 'check' : 'info');
    const main = probeFresh ? p.main_page : null, image = probeFresh ? p.image_service : null;
    // The image host has no public index: an HTTP response proves reachability, not upload authorization.
    const schoolOK = main?.ok && image?.state === 'reachable';
    badge('schoolBadge', !probeFresh ? '待检查' : schoolOK ? '可达' : '异常', !probeFresh ? 'neutral' : schoolOK ? 'good' : 'bad');
    $('schoolDetail').textContent = probeFresh ? '主站 ' + endpoint(main) + ' · 图片服务 ' + endpoint(image) : p ? '上次检查 ' + date(p.checked_at) : '连接后自动检查学校服务';
    $('schoolDetail').title = '检查主站页面和图片服务能否响应；图片服务根路径可能返回 HTTP 404，不代表上传接口不可用。';
    $('schoolRing').innerHTML = icon(!probeFresh ? 'info' : schoolOK ? 'check' : 'alert');
    const authState = !fresh ? '待验证' : credentials?.state === 'missing' ? '未导入' : credentials?.state === 'unreadable' ? '无法读取' : auth?.state === 'valid' ? '有效' : auth?.state === 'failed' ? '验证失败' : '待验证';
    const authTone = authState === '有效' ? 'good' : ['验证失败','无法读取'].includes(authState) ? 'bad' : 'neutral';
    badge('authBadge', authState, authTone); chip('credentialChip', '网站凭证' + authState, authTone);
    $('authDetail').textContent = !fresh ? '连接后验证网站凭证' : credentials?.state === 'missing' ? '导入网站导出的凭证 JSON' : auth?.state === 'valid' ? '模型目录已验证 · ' + Math.max(0, Math.floor(data.server_time - p.checked_at)) + ' 秒前' : auth?.error_code || (credentials?.state === 'unreadable' ? '请重新导入凭证' : '尚未确认凭证有效性');
    $('authDetail').title = '验证模型目录访问权限；图片上传凭证需通过实际上传验证。';
    $('runtimeStatus').textContent = g ? '网关 v' + g.version + (fresh ? ' · 运行正常' : ' · 上次记录') : '等待连接';
    $('updateStatus').textContent = !verified ? '输入密钥后连接' : paused ? '自动刷新已暂停' : document.hidden ? '后台已暂停' : !connected ? '连接中断，正在重试' : '每 3 秒刷新';
    $('lastUpdated').textContent = updated ? new Date(updated).toLocaleTimeString('zh-CN', {hourCycle:'h23'}) + ' 更新' : '尚未更新';
    document.body.classList.toggle('paused', paused || !fresh || document.hidden);
    const message = connectionMessage || probeMessage || (verified && !fresh && data ? '状态已过期，当前展示上次收到的数据。' : '');
    $('connectionStatus').hidden = !message; $('connectionStatus').textContent = message;
    const available = stats?.available && stats.window === range;
    $('successCount').textContent = available ? fmt(stats.complete) : '—';
    $('totalCount').textContent = available ? fmt(stats.total) : '—';
    $('successRate').textContent = available && stats.total ? stats.success_rate.toFixed(1) + '%' : '—';
    $('successRate').title = available ? '成功 ' + stats.complete + '，失败 ' + stats.failed + '，取消 ' + stats.cancelled : '暂无统计';
    $('latency').textContent = available && stats.average_duration_ms !== null ? (stats.average_duration_ms / 1000).toFixed(2) : '—';
    $('activeCount').textContent = g ? fmt(g.active_requests) : '—';
    $('concurrencyLimit').textContent = g ? fmt(g.max_concurrent_requests) : '—';
    $('queueCount').textContent = g ? fmt(g.queued_requests) : '—';
    const seconds = g?.uptime_seconds || 0;
    const units = seconds >= 86400 ? [Math.floor(seconds/86400), '天', Math.floor(seconds/3600)%24, '小时'] : seconds >= 3600 ? [Math.floor(seconds/3600),'小时', Math.floor(seconds/60)%60,'分钟'] : [Math.floor(seconds/60),'分钟',seconds%60,'秒'];
    ['uptimeMajor','uptimeMajorUnit','uptimeMinor','uptimeMinorUnit'].forEach((id, i) => $(id).textContent = g ? units[i] : i%2 ? units[i] : '—');
    $('statsCoverage').textContent = !data ? '仅统计已认证的模型请求；进行中和排队请求单独显示。' : !stats?.available ? '统计存储不可用，请检查网关私有目录权限。' : '仅统计已认证的模型请求；已结束包含成功、失败和取消。' + (!stats.complete_window ? ' 统计始于 ' + date(stats.started_at) + '。' : '');
  }
  async function refreshStatus() {
    const sequence = ++statusSequence, version = revision, selectedRange = range, start = performance.now();
    statusPending++;
    try {
      const result = await api('/v1/admin/status?window=' + selectedRange);
      if (sequence !== statusSequence || version !== revision || selectedRange !== range) return;
      data = result; updated = Date.now(); latency = Math.round(performance.now() - start);
      verified = connected = true; connectionMessage = ''; render();
    } catch (e) {
      if (e.stale || sequence !== statusSequence || version !== revision) return;
      connected = false; if (e.status === 401) verified = false;
      connectionMessage = e.message; render();
    } finally { statusPending--; }
  }
  async function probe(automatic = false) {
    if (probePending || !connected || (automatic && (ui.busy || ui.managing || data?.gateway.pending_requests > 0))) return;
    probePending = true; lastProbeAttempt = Date.now(); $('probe').disabled = true;
    const version = revision;
    probeMessage = '正在检查学校连接与网站凭证…'; render();
    try { await api('/v1/admin/probe' + (automatic ? '?automatic=true' : ''), {method:'POST'}); if (version === revision) probeMessage = ''; }
    catch (e) { if (!e.stale && version === revision) probeMessage = e.status === 409 ? '其他连接检查正在进行，稍后自动更新。' : e.message; }
    finally { probePending = false; $('probe').disabled = false; if (version === revision) await refreshStatus(); }
  }
  function maybeProbe() {
    if (Date.now() - lastProbeAttempt >= 60000 && (!data?.last_probe || data.server_time - data.last_probe.checked_at >= 60)) void probe(true);
  }
  function cellLines(primary, secondary = '', outcome = '') {
    const content = document.createElement('div'); content.className = 'cell-lines';
    const main = document.createElement('span'); main.textContent = primary;
    if (outcome) main.dataset.outcome = outcome; content.append(main);
    if (secondary) { const detail = document.createElement('span'); detail.className = 'cell-secondary'; detail.textContent = secondary; content.append(detail); }
    return content;
  }
  function row(target, values) {
    const tr = document.createElement('tr');
    for (const value of values) { const td = document.createElement('td'); if (value instanceof Node) td.append(value); else td.textContent = value; tr.append(td); }
    target.append(tr);
  }
  async function logs() {
    const sequence = ++logSequence;
    try {
      const params = new URLSearchParams({offset:logOffset, category:$('logCategory').value, outcome:$('logOutcome').value});
      const result = await api('/v1/admin/logs?' + params);
      if (sequence !== logSequence) return;
      $('logRows').replaceChildren(); $('activeRequests').replaceChildren();
      for (const r of result.active) { const p = document.createElement('p'); p.textContent = '进行中：' + r.method + '，' + (r.model || '') + '，' + r.request_id; $('activeRequests').append(p); }
      for (const r of result.items) {
        const details = document.createElement('details'), summary = document.createElement('summary'), body = document.createElement('textarea');
        summary.textContent = r.duration_ms + ' 毫秒'; body.readOnly = true; body.rows = 12; body.value = JSON.stringify(r, null, 2); body.setAttribute('aria-label','请求参数摘要'); details.append(summary, body);
        const [day, clock] = date(r.time).split(' ');
        row($('logRows'), [cellLines(day, clock), cellLines(r.method + ' ' + r.path, r.model), cellLines(label(r.outcome) + '（' + (r.http_status || '—') + '）', r.error_code, r.outcome), details]);
      }
      if (!result.items.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 4; td.className = 'empty-row'; td.textContent = '没有符合条件的请求'; tr.append(td); $('logRows').append(tr); }
      $('logMessage').textContent = result.available ? '已刷新；HTTP 状态与流式最终结果分别记录。' : '记录存储不可用，请检查私有目录权限。';
      $('logCount').textContent = `${result.total} 条，第 ${Math.floor(logOffset/50)+1} 页`;
      $('logPrevious').disabled = logOffset === 0; $('logNext').disabled = logOffset + 50 >= result.total;
    } catch (e) { if (!e.stale && sequence === logSequence) $('logMessage').textContent = e.message; }
  }
  function navigate(path, push = false) {
    const page = path === '/log' ? 'log' : path === '/test' ? 'test' : 'status';
    for (const name of ['status','log','test']) $(name + 'Page').hidden = page !== name;
    for (const link of document.querySelectorAll('nav a')) { if (link.getAttribute('href') === '/' + page) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); }
    if (push) history.pushState({}, '', '/' + page);
    document.title = ({status:'网关状态', log:'请求日志', test:'模型测试'}[page]) + ' · ShanghaiTech GenAI';
    if (verified && page === 'log') void logs();
    if (verified && page === 'status') void refreshStatus();
  }
  document.querySelector('nav').addEventListener('click', e => { const a = e.target.closest('a'); if (!a || e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate(a.getAttribute('href'), true); });
  document.querySelector('.brand').addEventListener('click', e => { if (e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate('/status', true); });
  window.addEventListener('popstate', () => navigate(location.pathname));
  $('apiKey').addEventListener('input', () => {
    revision++; statusSequence++; logSequence++; verified = connected = false; data = null; updated = 0; lastProbeAttempt = 0;
    connectionMessage = probeMessage = ''; $('credentialsStatus').hidden = true;
    $('logRows').replaceChildren(); $('activeRequests').replaceChildren(); $('logCount').textContent = ''; $('logMessage').textContent = '请验证网关密钥后刷新。';
    ui.credentialsChanged?.(); render();
  });
  $('toggleKey').addEventListener('click', () => {
    const visible = $('apiKey').type === 'password'; $('apiKey').type = visible ? 'text' : 'password';
    $('toggleKey').innerHTML = icon(visible ? 'eye-off' : 'eye'); $('toggleKey').setAttribute('aria-pressed', String(visible));
    $('toggleKey').setAttribute('aria-label', visible ? '隐藏密钥' : '显示密钥'); $('toggleKey').title = visible ? '隐藏密钥' : '显示密钥';
  });
  $('apiKey').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('connect').click(); } });
  $('connect').addEventListener('click', async () => { $('connect').disabled = true; try { await refreshStatus(); if (connected) { maybeProbe(); if (!$('logPage').hidden) await logs(); } } finally { $('connect').disabled = false; } });
  $('refreshStatus').addEventListener('click', async () => { await refreshStatus(); if (connected) maybeProbe(); });
  $('probe').addEventListener('click', () => { if (!connected) { connectionMessage = '请先验证网关连接。'; render(); } else void probe(); });
  $('pause').addEventListener('click', () => {
    paused = !paused; $('pause').innerHTML = icon(paused ? 'play' : 'pause');
    const title = paused ? '恢复自动刷新' : '暂停自动刷新'; $('pause').title = title; $('pause').setAttribute('aria-label', title); render();
    if (!paused && verified) void refreshStatus().then(maybeProbe);
  });
  document.querySelectorAll('[data-range]').forEach(b => b.addEventListener('click', () => {
    range = b.dataset.range; document.querySelectorAll('[data-range]').forEach(el => el.setAttribute('aria-pressed', String(el === b))); render(); if (verified) void refreshStatus();
  }));
  $('refreshLogs').addEventListener('click', logs);
  for (const [button, direction] of [['logPrevious', -1], ['logNext', 1]]) $(button).addEventListener('click', () => { logOffset = Math.max(0, logOffset + direction * 50); void logs(); });
  for (const id of ['logCategory','logOutcome']) $(id).addEventListener('change', () => { logOffset = 0; void logs(); });
  function credentialMessage(message) { $('credentialsStatus').textContent = message; $('credentialsStatus').hidden = !message; }
  function setManaging(value) { ui.managing = value; ui.syncControls?.(); }
  $('importCredentials').addEventListener('click', () => {
    if (ui.busy || ui.managing) return;
    if (!$('apiKey').value.trim()) { credentialMessage('请先输入网关密钥。'); return; }
    $('credentialsFile').click();
  });
  $('credentialsFile').addEventListener('change', async () => {
    const file = $('credentialsFile').files[0]; if (!file || ui.busy || ui.managing) return;
    setManaging(true);
    try {
      if (!file.size || file.size > 65536) throw new Error('请选择不超过 64 KiB 的凭证 JSON。');
      await api('/v1/credentials/import', {method:'POST', headers:{'Content-Type':'application/json'}, body:file});
      ui.credentialsChanged?.(); credentialMessage('凭证已保存，正在验证…'); lastProbeAttempt = 0;
      await refreshStatus(); await probe(); credentialMessage('凭证已保存，验证结果见网站凭证状态。');
    } catch (e) { if (!e.stale) credentialMessage(e.message); }
    finally { $('credentialsFile').value = ''; setManaging(false); }
  });
  $('logoutCredentials').addEventListener('click', async () => {
    if (ui.busy || ui.managing) return;
    setManaging(true);
    try { await api('/v1/credentials/logout', {method:'POST'}); ui.credentialsChanged?.(); credentialMessage('已取消本网关登录。'); await refreshStatus(); }
    catch (e) { if (!e.stale) credentialMessage(e.message); }
    finally { setManaging(false); }
  });
  async function tick() {
    if (!verified || paused || document.hidden || statusPending) { render(); return; }
    await refreshStatus(); if (connected) maybeProbe();
  }
  setInterval(tick, 3000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void tick(); else render(); });
  navigate(location.pathname); render();
})();
