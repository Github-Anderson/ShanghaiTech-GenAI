'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {resolveGateway, registerPrivateDir} = require('./gateway-client.cjs');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gateway-client-'));
try {
  const privateDir = path.join(root, 'private'); fs.mkdirSync(privateDir);
  const config = path.join(privateDir, 'dsh-gateway.json');
  assert.equal(resolveGateway(privateDir).keyFile, path.join(privateDir, 'gateway.key'));
  for (const base_url of ['', ' ', null]) {
    fs.writeFileSync(config, JSON.stringify({base_url, key_file:'remote.key'}));
    assert.equal(resolveGateway(privateDir).remote, false);
    assert.equal(resolveGateway(privateDir).keyFile, path.join(privateDir, 'gateway.key'));
  }
  fs.writeFileSync(config, JSON.stringify({base_url:'https://example.invalid/proxy',key_file:'remote.key'}));
  assert.deepEqual(resolveGateway(privateDir), {baseUrl:'https://example.invalid/proxy/v1',keyFile:path.join(privateDir,'remote.key'),remote:true});
  registerPrivateDir(privateDir, {DSH_HOME:root});
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root,'shanghaitech-genai.json'))), {private_dir:privateDir});
  for (const key_file of ['../outside', '/outside', '']) {
    fs.writeFileSync(config, JSON.stringify({base_url:'https://example.invalid',key_file}));
    assert.throws(()=>resolveGateway(privateDir), /Invalid private/);
  }
  console.log('PASS: gateway client configuration, blank local default, paired key selection and directory registration.');
} finally {fs.rmSync(root,{recursive:true,force:true});}
