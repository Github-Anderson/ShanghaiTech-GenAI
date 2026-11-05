'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

function resolveGateway(privateDir) {
  try {
    let config = {};
    try { config = JSON.parse(fs.readFileSync(path.join(privateDir, 'dsh-gateway.json'), 'utf8').replace(/^\uFEFF/, '')); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (!config || Array.isArray(config) || typeof config !== 'object') throw Error();
    if (config.base_url != null && typeof config.base_url !== 'string') throw Error();
    const remote = Boolean(config.base_url?.trim());
    let url = new URL(remote ? config.base_url.trim() : 'http://127.0.0.1:8000/v1');
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw Error();
    url.pathname = url.pathname.replace(/\/+$/, '');
    if (!url.pathname.endsWith('/v1')) url.pathname += '/v1';
    const name = remote ? config.key_file : 'gateway.key';
    if (typeof name !== 'string' || !name || path.isAbsolute(name) || name.split(/[\\/]/).some(p => p === '..' || !p)) throw Error();
    return {baseUrl: url.toString().replace(/\/$/, ''), keyFile: path.join(privateDir, name), remote};
  } catch {
    throw Error('Invalid private/dsh-gateway.json: check base_url and key_file.');
  }
}

function registerPrivateDir(privateDir, env = process.env) {
  const root = env.DSH_HOME || path.join(os.homedir(), '.dsh');
  fs.mkdirSync(root, {recursive: true});
  const dest = path.join(root, 'shanghaitech-genai.json');
  const temp = dest + '.' + process.pid + '.tmp';
  try {
    fs.writeFileSync(temp, JSON.stringify({private_dir: path.resolve(privateDir)}) + '\n', {mode: 0o600, flag: 'wx'});
    fs.renameSync(temp, dest);
  } finally {
    if (fs.existsSync(temp)) fs.unlinkSync(temp);
  }
}
const CONNECTION_RECORD = 'dsh-shanghaitech-genai/gateway';
async function syncGatewayCredentials(provider, privateDir, {replace = false, env = process.env} = {}) {
  const {baseUrl, keyFile} = resolveGateway(privateDir);
  const apiKey = fs.readFileSync(keyFile, 'utf8').replace(/^\uFEFF/, '').trim();
  if (apiKey.length < 32 || apiKey.length > 4096 || /[^\x21-\x7e]/.test(apiKey)) throw Error('Invalid gateway key file.');
  // The provider locks the complete address/key record. Ordinary startup must
  // preserve settings saved by the UI, including its optimistic-write revision.
  await provider.modifyRecord(CONNECTION_RECORD, record => {
    if (record !== undefined && !replace) return record;
    return {kind:'grant', payload:{baseUrl, apiKey, revision:require('node:crypto').randomUUID()}};
  });
  // Released plugins still consume this reference and private-directory registration.
  await provider.set('SHANGHAITECH_GENAI_API_KEY', apiKey);
  registerPrivateDir(privateDir, env);
}
module.exports = {resolveGateway, registerPrivateDir, syncGatewayCredentials, CONNECTION_RECORD};
