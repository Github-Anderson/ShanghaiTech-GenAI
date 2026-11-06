(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const ui = window.gatewayUI = {busy: false};
  let revision = 0, loginOffset = 0, logOffset = 0, managing = false;
  const date = value => value ? new Date(value * 1000).toLocaleString('zh-CN', {year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'}).replaceAll('/', '-') : '—';
  const labels = {complete:'完成', failed:'失败', cancelled:'断开 / 取消', valid:'有效', saved:'已保存', missing:'未导入', unreadable:'无法读取', unverified:'未验证', reachable:'已收到响应', unreachable:'连接失败', timeout:'超时', credential_import:'导入凭证', credential_logout:'取消登录', credential_check:'验证凭证', credential_rejected:'凭证不可用'};
  const label = value => labels[value] || value;
  async function api(path, options = {}) {
    const key = $('apiKey').value.trim();
    if (!key) throw new Error('请先输入网关密钥。');
    const version = revision;
    const response = await fetch(path, {...options, headers:{Authorization:'Bearer ' + key, ...options.headers}, credentials:'omit', cache:'no-store', signal:AbortSignal.timeout(40000)});
    if (version !== revision || key !== $('apiKey').value.trim()) throw new Error('密钥已更换，请重新验证。');
    const data = await response.json();
    if (!response.ok) throw new Error(response.status === 401 ? '网关密钥无效。' : (data.error?.message || '请求失败') + (data.error?.code ? '（' + data.error.code + '）' : ''));
    return data;
  }
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function statusCard(title, value, tone, details) {
    const card = element('section', 'status-card');
    card.dataset.tone = tone;
    const heading = element('div', 'status-card-heading');
    const icon = element('span', 'status-symbol', tone === 'good' ? '✓' : tone === 'bad' ? '!' : '—');
    icon.setAttribute('aria-hidden', 'true');
    heading.append(element('h3', '', title), icon);
    const list = element('dl', 'status-facts');
    for (const [name, value] of details) {
      const row = element('div'); row.append(element('dt', '', name), element('dd', '', value)); list.append(row);
    }
    card.append(heading, element('p', 'status-value', value), list);
    return card;
  }
  function uptime(seconds) {
    if (seconds < 60) return `${seconds} 秒`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} 分钟`;
    const hours = Math.floor(minutes / 60);
    return hours < 24 ? `${hours} 小时 ${minutes % 60} 分钟` : `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
  }
  function endpoint(result) {
    if (!result) return '待检查';
    if (result.state !== 'reachable') return label(result.state);
    return `HTTP ${result.http_status} / ${result.duration_ms} 毫秒`;
  }
  async function status() {
    const data = await api('/v1/admin/status');
    $('connectionStatus').textContent = '网关连接正常，当前密钥认证通过。';
    const target = $('statusSummary'); target.replaceChildren();
    const p = data.last_probe, g = data.gateway, auth = p?.website_authentication;
    const main = p?.main_page;
    const schoolState = !main ? '待检查' : main.state === 'reachable' ? (main.ok ? '连接正常' : '响应异常') : label(main.state);
    const authState = data.credentials.state === 'missing' ? '未导入' : data.credentials.state === 'unreadable' ? '无法读取' : auth?.state === 'valid' ? '认证通过' : auth?.state === 'failed' ? '认证失败' : '待验证';
    const connections = element('div', 'status-connections');
    connections.append(
      statusCard('网关连接', '运行正常', 'good', [['网关密钥', '认证通过'], ['当前版本', `v${g.version}`]]),
      statusCard('学校连接', schoolState, !main ? 'neutral' : main.ok ? 'good' : 'bad', [['学校页面', endpoint(main)], ['图片服务', endpoint(p?.image_service)]]),
      statusCard('网站凭证', authState, auth?.state === 'valid' ? 'good' : authState === '认证失败' || authState === '无法读取' ? 'bad' : 'neutral', [['凭证状态', label(data.credentials.state)], [auth?.error_code ? '错误代码' : '模型目录', auth?.error_code || (auth?.state === 'valid' ? `${auth.model_count} 个模型` : '—')]])
    );
    const runtime = element('dl', 'runtime-strip');
    const stats = [['请求队列', `${g.pending_requests}`, `活动与等待 / 并发上限 ${g.max_concurrent_requests}`], ['运行时长', uptime(g.uptime_seconds), `协议版本 ${g.protocol_version}`], ['日志存储', data.journal.available ? (data.journal.persistent ? '已持久化' : '内存存储') : '不可用', data.journal.available ? '请求与登录活动' : '请检查私有目录权限']];
    for (const [name, value, detail] of stats) {
      const item = element('div', 'runtime-item');
      item.append(element('dt', '', name), element('dd', '', value), element('dd', 'runtime-detail', detail)); runtime.append(item);
    }
    target.append(connections, runtime);
    $('probeTime').textContent = p ? '上次检查 ' + date(p.checked_at) : '学校连接尚未检查';
  }
  function cellLines(primary, secondary = '', outcome = '') {
    const content = document.createElement('div');
    content.className = 'cell-lines';
    const main = document.createElement('span');
    main.textContent = primary;
    if (outcome) main.dataset.outcome = outcome;
    content.append(main);
    if (secondary) {
      const detail = document.createElement('span');
      detail.className = 'cell-secondary'; detail.textContent = secondary;
      content.append(detail);
    }
    return content;
  }
  function dateCell(value) {
    const [day, clock] = date(value).split(' ');
    return cellLines(day, clock);
  }
  function row(target, values) {
    const tr = document.createElement('tr');
    for (const value of values) {
      const td = document.createElement('td');
      if (value instanceof Node) td.append(value); else td.textContent = value;
      tr.append(td);
    }
    target.append(tr);
  }
  async function history() {
    const data = await api('/v1/admin/login-history?offset=' + loginOffset);
    $('loginRows').replaceChildren();
    for (const r of data.items) row($('loginRows'), [dateCell(r.time), label(r.event), cellLines(label(r.result), r.error_code, r.result)]);
    $('loginCount').textContent = data.available ? `${data.total} 条，第 ${Math.floor(loginOffset/50)+1} 页` : '记录存储不可用';
    $('loginPrevious').disabled = loginOffset === 0; $('loginNext').disabled = loginOffset + 50 >= data.total;
  }
  async function logs() {
    const params = new URLSearchParams({offset:logOffset, category:$('logCategory').value, outcome:$('logOutcome').value});
    const data = await api('/v1/admin/logs?' + params);
    $('logRows').replaceChildren(); $('activeRequests').replaceChildren();
    for (const r of data.active) { const p = document.createElement('p'); p.textContent = '进行中：' + r.method + '，' + (r.model || '') + '，' + r.request_id; $('activeRequests').append(p); }
    for (const r of data.items) {
      const details = document.createElement('details'), summary = document.createElement('summary'), body = document.createElement('textarea');
      summary.textContent = r.duration_ms + ' 毫秒'; body.readOnly = true; body.rows = 12; body.cols = 28; body.value = JSON.stringify(r, null, 2); body.setAttribute('aria-label','请求参数摘要'); details.append(summary, body);
      row($('logRows'), [dateCell(r.time), cellLines(r.method + ' ' + r.path, r.model), cellLines(label(r.outcome) + '（' + (r.http_status || '—') + '）', r.error_code, r.outcome), details]);
    }
    $('logMessage').textContent = data.available ? '已刷新；HTTP 状态与流式最终结果分别记录。' : '记录存储不可用，请检查私有目录权限。';
    $('logCount').textContent = `${data.total} 条，第 ${Math.floor(logOffset/50)+1} 页`;
    $('logPrevious').disabled = logOffset === 0; $('logNext').disabled = logOffset + 50 >= data.total;
  }
  function action(button, fn, output) { $(button).addEventListener('click', async () => { $(button).disabled = true; try { await fn(); } catch (e) { $(output).textContent = e.message; $(button).disabled = false; } finally { if (!['loginPrevious','loginNext','logPrevious','logNext'].includes(button)) $(button).disabled = false; } }); }
  async function refreshCurrent() {
    if (!$('apiKey').value.trim()) return;
    try { if (!$('statusPage').hidden) { await status(); await history(); } else if (!$('logPage').hidden) await logs(); }
    catch (e) { $('connectionStatus').textContent = e.message; }
  }
  function navigate(path, push = false) {
    const page = path === '/log' ? 'log' : path === '/test' ? 'test' : 'status';
    for (const name of ['status','log','test']) $(name + 'Page').hidden = page !== name;
    for (const link of document.querySelectorAll('nav a')) { if (link.getAttribute('href') === '/' + page) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); }
    if (push) window.history.pushState({}, '', '/' + page);
    refreshCurrent();
  }
  document.querySelector('nav').addEventListener('click', e => { const a = e.target.closest('a'); if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate(a.getAttribute('href'),true); });
  window.addEventListener('popstate', () => navigate(location.pathname));
  $('apiKey').addEventListener('input', () => {
    revision++; $('connectionStatus').textContent = '密钥已更换，请重新验证。';
    $('statusSummary').replaceChildren(); $('probeTime').textContent = ''; $('probeMessage').textContent = ''; $('loginRows').replaceChildren(); $('logRows').replaceChildren(); $('activeRequests').replaceChildren();
    ui.credentialsChanged?.();
  });
  action('connect', async () => { await status(); await refreshCurrent(); }, 'connectionStatus');
  action('refreshStatus', status, 'connectionStatus');
  action('refreshLogin', history, 'connectionStatus');
  action('refreshLogs', logs, 'logMessage');
  action('probe', async () => { $('probeMessage').textContent = '正在检查学校连接与凭证…'; await api('/v1/admin/probe',{method:'POST'}); $('probeMessage').textContent = '';  await status(); await history(); }, 'probeMessage');
  for (const [button, dir] of [['logPrevious',-1],['logNext',1]]) action(button, async () => { logOffset = Math.max(0,logOffset+dir*50); await logs(); }, 'logMessage');
  for (const [button, dir] of [['loginPrevious',-1],['loginNext',1]]) action(button, async () => { loginOffset = Math.max(0,loginOffset+dir*50); await history(); }, 'connectionStatus');
  for (const id of ['logCategory','logOutcome']) $(id).addEventListener('change', () => { logOffset=0; logs().catch(e => $('logMessage').textContent=e.message); });
  function setManaging(value) { managing=value; ui.managing=value; ui.syncControls?.(); }
  $('importCredentials').addEventListener('click', () => { if (ui.busy || managing) return; if (!$('apiKey').value.trim()) { $('credentialsStatus').textContent='请先输入网关密钥。'; return; } $('credentialsFile').click(); });
  $('credentialsFile').addEventListener('change', async () => {
    const file=$('credentialsFile').files[0]; if (!file || ui.busy || managing) return;
    setManaging(true);
    try { if (!file.size || file.size>65536) throw new Error('请选择不超过 64 KiB 的凭证 JSON。');
      await api('/v1/credentials/import',{method:'POST', headers:{'Content-Type':'application/json'}, body:file});
      ui.credentialsChanged(); $('credentialsStatus').textContent='凭证已保存。可点击“检查学校连接与凭证”验证有效性。'; loginOffset=0; await status(); await history();
    } catch (e) { $('credentialsStatus').textContent=e.message; } finally { $('credentialsFile').value=''; setManaging(false); }
  });
  action('logoutCredentials', async () => { if (ui.busy || managing) throw new Error('请等待当前请求结束。'); setManaging(true); try { await api('/v1/credentials/logout',{method:'POST'}); ui.credentialsChanged(); $('credentialsStatus').textContent='已取消本网关登录。'; loginOffset=0; await status(); await history(); } finally { setManaging(false); } }, 'credentialsStatus');
  navigate(location.pathname);
})();
