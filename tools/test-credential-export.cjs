'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../userscript/export-credentials.user.js'), 'utf8');
const begin = source.indexOf('  function extractSigningSalt(source) {');
const end = source.indexOf('  async function readSigningSalt()', begin);
assert(begin >= 0 && end > begin);
// Execute only our extractor, never the downloaded website bundle.
const extract = vm.runInNewContext(source.slice(begin, end) + '\nextractSigningSalt');
function fixture(digest = 'i', saltVariable = 'h') {
  return `var ${saltVariable}="synthetic-test-salt",p=function(){function e(){l(this,e)}return u(e,null,[{key:"getSign",value:function(e,t){var n=this.parseQueryString(e),r=this.mergeObject(n,t),a=this.sortAsc(r);return ${digest}()(JSON.stringify(a)+${saltVariable}).toUpperCase()}}])}();`;
}
for (const digest of ['i', 'o', 'renamedDigest', '$digest']) {
  assert.deepEqual(Array.from(extract(fixture(digest))), ['synthetic-test-salt']);
}
assert.equal(extract(fixture('o', 'renamedSalt')).length, 1);
for (const changed of [
  fixture().replace('sortAsc(r)', 'differentSort(r)'),
  fixture().replace('stringify(a)+h', 'stringify(a)+otherSalt'),
  fixture().replace('.toUpperCase()', '.toLowerCase()'),
  fixture().replace('parseQueryString(e)', 'parseQueryString(t)'),
]) assert.equal(extract(changed).length, 0);
assert.equal(extract(fixture() + fixture('o')).length, 2, 'Caller must reject ambiguous matches');
if (process.argv[2]) {
  const website = fs.readFileSync(process.argv[2], 'utf8');
  const matches = extract(website);
  assert.equal(matches.length, 1, 'Current frontend must contain exactly one confirmed signing structure');
  assert(matches[0].length > 0 && matches[0].length <= 256 && /^[\x21-\x7e]+$/.test(matches[0]));
  console.log('PASS: current frontend signing structure (no credentials printed)');
}
console.log('PASS: renamed imports and salt binding; changed algorithms and ambiguity detection');
