'use strict';
const path = require('node:path');
const {createRequire} = require('node:module');
const {syncGatewayCredentials} = require('./tools/gateway-client.cjs');

async function main() {
  const keyPath = process.argv[2];
  if (!keyPath) throw Error();
  const privateDir = path.dirname(path.resolve(keyPath));
  const sdkRoot = path.join(path.dirname(process.execPath), 'resources/app.asar/dsh');
  const shipped = createRequire(path.join(sdkRoot, 'package.json'));
  const {Context} = shipped('@deepseek-ai/cordis');
  const {LocalCredentialProvider} = shipped('@deepseek-ai/dsh-credentials-local');
  const root = new Context();
  try {
    const provider = new LocalCredentialProvider(root, {watch: false});
    await syncGatewayCredentials(provider, privateDir, {replace:process.argv.includes('--replace-connection')});
    console.log('DSH gateway credential synchronized.');
  } finally {
    await root.fiber.dispose();
  }
}

main().catch(() => {
  console.error('Could not synchronize the DSH gateway credential. Check private/gateway.key and DSH installation. No key values were logged.');
  process.exitCode = 1;
});
