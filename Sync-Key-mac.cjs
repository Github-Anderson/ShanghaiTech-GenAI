'use strict';
const path = require('node:path');
const {createRequire} = require('node:module');
const {syncGatewayCredentials} = require('./tools/gateway-client.cjs');

async function main() {
  const privateDir = path.dirname(path.resolve(process.argv[2]));
  const shipped = createRequire(path.join(process.argv[3], 'app.asar/dsh/package.json'));
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
  console.error('Could not synchronize DSH gateway credential; no key values logged.');
  process.exitCode = 1;
});
