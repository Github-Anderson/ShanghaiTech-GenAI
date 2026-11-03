'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');

async function main() {
  const key = fs.readFileSync(process.argv[2], 'utf8').replace(/^\uFEFF/, '').trim();
  if (key.length < 32 || key.length > 4096 || /[^\x21-\x7e]/.test(key)) throw Error();
  const shipped = createRequire(path.join(process.argv[3], 'app.asar/dsh/package.json'));
  const {Context} = shipped('@deepseek-ai/cordis');
  const {LocalCredentialProvider} = shipped('@deepseek-ai/dsh-credentials-local');
  const root = new Context();
  try {
    const provider = new LocalCredentialProvider(root, {watch: false});
    await provider.set('SHANGHAITECH_GENAI_API_KEY', key);
    console.log('DSH gateway credential synchronized.');
  } finally {
    await root.fiber.dispose();
  }
}
main().catch(() => {
  console.error('Could not synchronize DSH gateway credential; no key values logged.');
  process.exitCode = 1;
});
