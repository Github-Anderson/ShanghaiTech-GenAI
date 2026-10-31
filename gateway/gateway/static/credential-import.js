(() => {
  'use strict';
  const button = document.getElementById('importCredentials');
  const input = document.getElementById('credentialsFile');
  const status = document.getElementById('credentialsStatus');
  const logout = document.getElementById('logoutCredentials');
  const keyInput = document.getElementById('apiKey');
  let importing = false;
  let revision = 0;
  button.addEventListener('click', () => {
    if (!document.getElementById('apiKey').value.trim()) {
      status.textContent = '请先填写上方的本地网关密钥。';
      return;
    }
    input.click();
  });
  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file || importing) return;
    importing = true;
    revision++;
    button.disabled = true;
    logout.disabled = true;
    try {
      if (!file.size || file.size > 65536) {
        status.textContent = '请选择不超过 64 KiB 的油猴凭证 JSON。';
        return;
      }
      status.textContent = '正在导入并保存凭证…';
      const response = await fetch('/v1/credentials/import', {
        method: 'POST', headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + document.getElementById('apiKey').value.trim()
        }, body: file, credentials: 'omit', cache: 'no-store'
      });
      if (!response.ok) {
        status.textContent = ({401: '网关密钥错误。', 403: '请在网关页面导入。',
          409: '请等待正在进行的聊天结束后再导入。',
          413: '凭证文件超过大小限制。', 503: '保存失败，请检查凭证目录或持久卷权限。'})[response.status]
          || '导入失败，请选择完整的油猴凭证导出 JSON；原凭证保持不变。';
        return;
      }
      const result = await response.json();
      status.textContent = '凭证已保存并立即生效，重启网关后仍保留。'
        + (result.image_upload_verified_at_export ? '导出时图片上传已验证。' : '导出时图片 Token 未验证。')
        + '本次导入未调用上游验证有效性。';
    } catch {
      status.textContent = '导入结果未确认，请检查网关连接后重新导入。';
    } finally {
      input.value = '';
      importing = false;
      button.disabled = false;
      logout.disabled = false;
    }
  });
  keyInput.addEventListener('change', async () => {
    const requestedRevision = revision;
    const key = keyInput.value.trim();
    if (!key || importing) return;
    try {
      const response = await fetch('/v1/credentials/status', {
        headers: { Authorization: 'Bearer ' + key }, credentials: 'omit', cache: 'no-store'
      });
      if (!response.ok || importing || revision !== requestedRevision || key !== keyInput.value.trim()) return;
      const result = await response.json();
      if (importing || revision !== requestedRevision || key !== keyInput.value.trim()) return;
      status.textContent = result.logged_in ? '已导入登录凭证。可重新导入更新，或取消登录。'
        : '当前未登录，请导入油猴导出的 JSON。';
    } catch { /* Keep the current message when a status lookup is unavailable. */ }
  });
  logout.addEventListener('click', async () => {
    if (importing) return;
    const key = keyInput.value.trim();
    if (!key) { status.textContent = '请先填写上方的本地网关密钥。'; return; }
    if (document.getElementById('newChat').disabled) {
      status.textContent = '请等待正在进行的聊天或附件上传结束后取消登录。';
      return;
    }
    importing = true;
    revision++;
    button.disabled = logout.disabled = true;
    try {
      const response = await fetch('/v1/credentials/logout', {
        method: 'POST', headers: { Authorization: 'Bearer ' + key }, credentials: 'omit', cache: 'no-store'
      });
      if (!response.ok) {
        status.textContent = response.status === 409 ? '请等待正在进行的聊天结束后取消登录。'
          : response.status === 401 ? '网关密钥错误。' : '取消登录失败，凭证可能仍保留，请重试。';
        return;
      }
      document.getElementById('newChat').click();
      input.value = '';
      status.textContent = '已取消登录，保存的主站和图片凭证已清除。原网站登录不受影响。';
    } catch { status.textContent = '取消登录结果未确认，请检查连接后重试。'; }
    finally {
      importing = false;
      button.disabled = logout.disabled = false;
    }
  });
})();
