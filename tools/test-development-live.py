#!/usr/bin/env python3
"""Opt-in local smoke checks. Run on stdin inside the development gateway.

Uses only configured non-GPT models, synthetic data and per-test conversation
IDs. Website test sessions and uploaded file handles are deleted after checks.
"""
import json
from pathlib import Path
import uuid
import httpx


def main():
    key = Path('/run/secrets/gateway_key').read_text().strip()
    results=[]
    with httpx.Client(base_url='http://127.0.0.1:8000/v1', headers={'Authorization':'Bearer '+key}, timeout=180, trust_env=False) as client:
        models={m['id'] for m in client.get('/models').json()['data']}
        for model in ('deepseek-pro','Kimi-k3','qwen-instruct','chatglm'):
            if model not in models:continue
            cid='development-smoke-'+uuid.uuid4().hex
            try:
                response=client.post('/chat/completions',json={'model':model,'conversation_id':cid,'stream':False,'max_tokens':256,
                    'messages':[{'role':'user','content':'Reply exactly LOCAL_GATEWAY_OK.'}], 'shanghaitech':{'search':False,'thinking':False}})
                data=response.json()
                text=data.get('choices',[{}])[0].get('message',{}).get('content') or ''
                results.append({'case':'non_gpt_smoke','model':model,'http':response.status_code,'passed':response.status_code==200 and 'LOCAL_GATEWAY_OK' in text,'error_code':data.get('error',{}).get('code')})
            finally:
                response=client.delete('/conversations/'+cid)
                results.append({'case':'cleanup','model':model,'passed':response.status_code==200 and response.json().get('deleted') is True})
        cid='development-attachment-'+uuid.uuid4().hex
        file_id=None
        try:
            marker='LOCAL_ATTACHMENT_'+uuid.uuid4().hex
            response=client.post('/files',data={'model':'deepseek-pro','purpose':'user_data','conversation_id':cid},files={'file':('development-probe.txt',marker.encode(),'text/plain')})
            data=response.json();file_id=data.get('id')
            passed=response.status_code==200 and data.get('status')=='ready' and marker in data.get('parsed_content','')
            results.append({'case':'txt_upload_parse','http':response.status_code,'passed':passed,'error_code':data.get('error',{}).get('code')})
            if passed:
                response=client.post('/chat/completions',json={'model':'deepseek-pro','conversation_id':cid,'max_tokens':256,'messages':[{'role':'user','content':[{'type':'text','text':'Return only the exact LOCAL_ATTACHMENT marker in the document.'},{'type':'file','file':{'file_id':file_id}}]}]})
                data=response.json();text=data.get('choices',[{}])[0].get('message',{}).get('content') or ''
                results.append({'case':'txt_attachment_chat','http':response.status_code,'passed':response.status_code==200 and marker in text,'error_code':data.get('error',{}).get('code')})
        finally:
            if file_id:
                response=client.delete('/files/'+file_id)
                results.append({'case':'file_cleanup','passed':response.status_code==200})
            response=client.delete('/conversations/'+cid)
            results.append({'case':'attachment_conversation_cleanup','passed':response.status_code==200 and response.json().get('deleted') is True})
    for row in results:print(json.dumps(row),flush=True)
    failed=any(not row['passed'] for row in results)
    print(json.dumps({'case':'summary','passed':not failed,'checks':len(results)}))
    return failed


if __name__=='__main__':
    raise SystemExit(main())
