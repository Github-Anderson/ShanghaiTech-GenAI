'use strict';
// Synthetic files only. Set DSH_TEST_SDK_ROOT under Electron-as-Node for native-store coverage.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {createRequire}=require('node:module');
const {syncGatewayCredentials,CONNECTION_RECORD,resolveGateway}=require('./gateway-client.cjs');
async function exercise(provider,root) {
  const privateDir=path.join(root,'private');fs.mkdirSync(privateDir);
  const config=path.join(privateDir,'dsh-gateway.json'), keyFile=path.join(privateDir,'remote.key');
  const key='synthetic-migration-key-'.repeat(2), edited='synthetic-ui-key-'.repeat(3);
  fs.writeFileSync(config,JSON.stringify({base_url:'https://original.invalid/proxy',key_file:'remote.key'}));fs.writeFileSync(keyFile,key);
  const opts={env:{DSH_HOME:root}};
  await syncGatewayCredentials(provider,privateDir,opts);
  const first=await provider.readRecord(CONNECTION_RECORD);
  assert.equal(first.payload.baseUrl,'https://original.invalid/proxy/v1');assert.equal(first.payload.apiKey,key);
  assert.equal((await provider.resolve('SHANGHAITECH_GENAI_API_KEY')).value,key);
  // An old released plugin still resolves both its private directory and key.
  const registered=JSON.parse(fs.readFileSync(path.join(root,'shanghaitech-genai.json'))).private_dir;
  assert.equal(resolveGateway(registered).baseUrl,first.payload.baseUrl);
  assert.equal(fs.readFileSync(resolveGateway(registered).keyFile,'utf8'),key);
  const saved={kind:'grant',payload:{baseUrl:'https://ui.invalid/v1',apiKey:edited,revision:'ui-revision'}};
  await provider.modifyRecord(CONNECTION_RECORD,()=>saved);
  for(let i=0;i<2;i++)await syncGatewayCredentials(provider,privateDir,opts);
  assert.deepEqual(await provider.readRecord(CONNECTION_RECORD),saved,'Update/start preserve UI values and revision');
  await syncGatewayCredentials(provider,privateDir,{...opts,replace:true});
  const replaced=await provider.readRecord(CONNECTION_RECORD);
  assert.equal(replaced.payload.baseUrl,first.payload.baseUrl);assert.equal(replaced.payload.apiKey,key);
  assert.notEqual(replaced.payload.revision,saved.payload.revision);
  fs.writeFileSync(keyFile,'short');
  await assert.rejects(syncGatewayCredentials(provider,privateDir,{...opts,replace:true}),/Invalid gateway key/);
  assert.deepEqual(await provider.readRecord(CONNECTION_RECORD),replaced);
  fs.writeFileSync(keyFile,key);
  fs.writeFileSync(path.join(privateDir,'gateway.key'),key);
  fs.writeFileSync(config,JSON.stringify({base_url:'',key_file:'remote.key'}));
  await syncGatewayCredentials(provider,privateDir,{...opts,replace:true});
  assert.equal((await provider.readRecord(CONNECTION_RECORD)).payload.baseUrl,'http://127.0.0.1:8000/v1');
  return await provider.readRecord(CONNECTION_RECORD);
}
async function main(){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'genai-sync-test-'));let ctx,other;
 try{
  if(process.env.DSH_TEST_SDK_ROOT){
   const sdk=createRequire(path.join(process.env.DSH_TEST_SDK_ROOT,'package.json'));
   const {Context}=sdk('@deepseek-ai/cordis'),{LocalCredentialProvider}=sdk('@deepseek-ai/dsh-credentials-local');
   ctx=new Context();other=new Context();
   const options={watch:false,dshHome:root,path:path.join(root,'.credentials.yaml')};
   await ctx.plugin(LocalCredentialProvider,options).await();
   const expected=await exercise(ctx.credentials,root);
   await other.plugin(LocalCredentialProvider,options).await();
   assert.deepEqual(await other.credentials.readRecord(CONNECTION_RECORD),expected);
   console.log('PASS: native DSH store migration, restart persistence, preservation, explicit replacement, legacy compatibility and validation');
  }else{
   const records=new Map(),values=new Map();
   await exercise({modifyRecord:async(id,fn)=>records.set(id,await fn(records.get(id))),readRecord:async id=>records.get(id),set:async(id,v)=>values.set(id,v),resolve:async id=>({value:values.get(id)})},root);
   console.log('PASS: connection migration, preservation, explicit replacement, legacy compatibility and validation');
  }
 }finally{await other?.fiber.dispose();await ctx?.fiber.dispose();fs.rmSync(root,{recursive:true,force:true});}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
