"""Management contracts using isolated credentials and mocked school responses."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from gateway.app import app, Service
from gateway.audit import AuditStore, AuditMiddleware, request_summary
from gateway.settings import Settings
from gateway.protocol import GatewayError
from gateway.admin import probe_connections, snapshot
from test_frontend_alignment import records, settings_response, dictionary, MODELS

KEY = 'test-gateway-key-' * 3
SECRET = 'NEVER-STORE-THIS-CONTENT'
CREDS = {'schema':'shanghaitech-browser-credentials/v1',
         'main':{'origin':'https://genai.shanghaitech.edu.cn','headers':{'X-Access-Token':SECRET, 'tenant-id':'test-tenant'}},
         'image':{'origin':'https://genaipic.shanghaitech.edu.cn','headers':{'token':SECRET},'upload_verified':False},
         'signing':{'salt':SECRET}}


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'key').write_text(KEY)
        self.upstream_calls = []
        def upstream(req):
            self.upstream_calls.append(req.url.path)
            if req.url.path == '/': return httpx.Response(200, text='Page')
            result = records() if req.url.path.endswith('/list') else settings_response() if req.url.path.endswith('/detail') else dictionary()
            return httpx.Response(200, json=result)
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        self.service = Service(Settings(self.root/'key', imported_credentials_file=self.root/'credentials.json', models=MODELS, dynamic_site_config=True), self.upstream)
        self.service.credential_store.save(CREDS)
        self.state = patch.object(app.state,'service',self.service,create=True)
        self.state.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Authorization':'Bearer '+KEY})

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.upstream.aclose()
        self.state.stop()
        self.service.audit.close()
        self.tmp.cleanup()

    async def test_auth_status_and_explicit_probe(self):
        for path in ['/v1/admin/status','/v1/admin/logs','/v1/admin/login-history']:
            denied=await self.client.get(path, headers={'Authorization':'Bearer wrong'})
            self.assertEqual(denied.status_code,401)
        status=(await self.client.get('/v1/admin/status')).json()
        self.assertTrue(status['credentials']['saved'])
        self.assertIsNone(status['last_probe'])
        self.assertEqual(self.upstream_calls,[])
        bad=await self.client.post('/v1/admin/probe',headers={'Origin':'https://other.example'})
        self.assertEqual(bad.status_code,403)
        probe=await self.client.post('/v1/admin/probe')
        self.assertEqual(probe.status_code,200)
        self.assertEqual(probe.json()['website_authentication']['state'],'valid')
        self.assertEqual(probe.json()['image_credential'],'unverified')
        self.assertEqual(len(self.upstream_calls),5)
        self.assertFalse(any('chat/start' in p or 'upload' in p for p in self.upstream_calls))
        status=(await self.client.get('/v1/admin/status')).json()
        self.assertEqual(status['last_probe']['website_authentication']['model_count'],4)
        history=(await self.client.get('/v1/admin/login-history')).json()
        self.assertEqual(history['items'][0]['event'],'credential_check')

    async def test_login_history_rotation_and_failed_import(self):
        self.assertEqual((await self.client.get('/v1/admin/login-history')).json()['total'],0)
        await self.client.post('/v1/admin/probe')
        result=await self.client.post('/v1/credentials/import',json={'secret':SECRET})
        self.assertEqual(result.status_code,400)
        self.assertEqual((await self.client.get('/v1/admin/login-history')).json()['items'][0]['result'],'failed')
        result=await self.client.post('/v1/credentials/import',json=CREDS)
        self.assertEqual(result.status_code,200)
        self.assertIsNone((await self.client.get('/v1/admin/status')).json()['last_probe'])
        result=await self.client.post('/v1/credentials/logout')
        self.assertEqual(result.status_code,200)
        self.assertFalse((await self.client.get('/v1/admin/status')).json()['credentials']['saved'])
        all_rows=self.service.audit.rows('login')['items']
        self.assertEqual([r['event'] for r in all_rows], ['credential_logout','credential_import','credential_import','credential_check'])

    async def test_request_redaction_filters_and_route_templates(self):
        await self.client.get('/private/'+SECRET+'?key='+SECRET)
        await self.client.delete('/v1/conversations/'+SECRET)
        data={'model':'deepseek-pro','messages':[{'role':'user','content':SECRET}], 'temperature':0.5, 'shanghaitech':{'search':False,'thinking':True}}
        response=await self.client.post('/v1/chat/completions',json=data)
        self.assertEqual(response.status_code,400)
        rows=(await self.client.get('/v1/admin/logs?category=api&outcome=failed')).json()['items']
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['model'],'deepseek-pro')
        self.assertEqual(rows[0]['messages_count'],1)
        self.assertEqual(rows[1]['path'],'/v1/conversations/{conversation_id}')
        self.assertEqual(rows[2]['path'],'<unmatched>')
        self.assertNotIn(SECRET,json.dumps(rows))
        self.assertNotIn(KEY,json.dumps(rows))
        self.assertNotIn(SECRET.encode(), (self.root/'admin.sqlite3').read_bytes())
        self.assertEqual((await self.client.get('/v1/admin/logs?limit=101')).status_code,422)
        page=(await self.client.get('/v1/admin/logs?category=api&limit=1&offset=1')).json()
        self.assertEqual(len(page['items']),1)
        self.assertEqual(page['total'],3)

    async def test_stream_final_failure_is_not_http_success(self):
        async def failed(*args,**kwargs):
            yield {'content':'partial'}
            raise GatewayError(502,'upstream_incomplete','Incomplete')
        with patch.object(self.service,'events',failed):
            response=await self.client.post('/v1/chat/completions',json={'model':'deepseek-pro','stream':True,'messages':[{'role':'user','content':SECRET}]})
        self.assertEqual(response.status_code,200)
        self.assertIn('upstream_incomplete',response.text)
        row=self.service.audit.rows('request')['items'][0]
        self.assertEqual(row['http_status'],200)
        self.assertEqual(row['outcome'],'failed')
        self.assertEqual(row['error_code'],'upstream_incomplete')
        self.assertEqual(response.headers['x-request-id'],row['request_id'])

    async def test_success_stream_and_nonstream(self):
        async def success(*args,**kwargs):
            yield {'content':'Hello'}
            yield {'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
            yield {'finish_reason':'stop'}
        for stream in [True,False]:
            with patch.object(self.service,'events',success):
                response=await self.client.post('/v1/chat/completions',json={'model':'deepseek-pro','stream':stream,'messages':[{'role':'user','content':SECRET}]})
            self.assertEqual(response.status_code,200)
            self.assertEqual(self.service.audit.rows('request')['items'][0]['outcome'],'complete')

    async def test_disconnect_keeps_partial_request_cancelled(self):
        sent=[]
        async def downstream(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'partial','more_body':True})
            await receive()
        async def receive(): return {'type':'http.disconnect'}
        async def send(message): sent.append(message)
        scope={'type':'http','method':'POST','path':'/anything','app':app}
        await AuditMiddleware(downstream)(scope,receive,send)
        row=self.service.audit.rows('request')['items'][0]
        self.assertEqual(row['outcome'],'cancelled')
        self.assertEqual(self.service.audit.active,{})

    async def test_probe_auth_failure_and_credential_change(self):
        async def denied(**kwargs): raise GatewayError(503,'upstream_authentication_required','Renew')
        with patch.object(self.service,'site_config',denied):
            result=await probe_connections(self.service)
        self.assertEqual(result['website_authentication']['state'],'failed')
        self.assertEqual(result['main_page']['state'],'reachable')
        changed=dict(CREDS, signing={'salt':'different-salt'})
        self.service.credential_store.save(changed)
        self.assertIsNone(snapshot(self.service)['last_probe'])
        async def rotation(**kwargs):
            self.service.credential_store.save(CREDS)
            from gateway.site_config import SiteConfig
            return SiteConfig(MODELS)
        with patch.object(self.service,'site_config',rotation):
            with self.assertRaises(GatewayError) as caught: await probe_connections(self.service)
        self.assertEqual(caught.exception.code,'credentials_changed')

    async def test_pages_and_shared_styles_are_served(self):
        for path in ['/status','/log','/test']:
            response=await self.client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn('href="/style.css"',response.text)
            self.assertNotIn('style=',response.text)
        css = await self.client.get('/style.css')
        self.assertEqual(css.status_code, 200)
        self.assertIn('text/css', css.headers['content-type'])


class AuditPersistenceTests(unittest.TestCase):
    def test_retention_pagination_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'audit.db'
            first=AuditStore(path)
            first.REQUEST_LIMIT=3
            for i in range(5): first.add('request',{'request_id':str(i)})
            first.login('credential_import','saved')
            self.assertEqual(first.rows('request')['total'],3)
            first.db.execute('UPDATE events SET time=? WHERE kind=?',(time.time()-8*86400,'request'))
            first.db.commit()
            self.assertEqual(first.rows('request')['total'],0)
            first.close()
            second=AuditStore(path)
            self.assertEqual(second.rows('login')['items'][0]['event'],'credential_import')
            second.close()

    def test_unknown_values_never_enter_summary_and_storage_failure_is_visible(self):
        safe=request_summary({'model':SECRET,'stream':SECRET,'max_tokens':True,'tools':[SECRET],'shanghaitech':{'thinking':SECRET,'secret':SECRET}},MODELS)
        self.assertNotIn(SECRET,json.dumps(safe))
        with tempfile.TemporaryDirectory() as folder:
            store=AuditStore(Path(folder))
            self.assertFalse(store.rows('request')['available'])
            store.add('request',{})
            store.close()
