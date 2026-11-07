"""Management contracts using isolated credentials and mocked school responses."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from gateway.app import app
from gateway.service import Service
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
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['model'],'deepseek-pro')
        self.assertEqual(rows[0]['messages_count'],1)
        self.assertEqual(rows[1]['path'],'/v1/conversations/{conversation_id}')
        unmatched=(await self.client.get('/v1/admin/logs?category=management&outcome=failed')).json()['items']
        self.assertEqual([r['path'] for r in unmatched],['<unmatched>'])
        self.assertNotIn(SECRET,json.dumps(rows))
        self.assertNotIn(KEY,json.dumps(rows))
        self.assertNotIn(SECRET.encode(), (self.root/'admin.sqlite3').read_bytes())
        self.assertEqual((await self.client.get('/v1/admin/logs?limit=101')).status_code,422)
        page=(await self.client.get('/v1/admin/logs?category=api&limit=1&offset=1')).json()
        self.assertEqual(len(page['items']),1)
        self.assertEqual(page['total'],2)

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
        self.assertEqual(self.service.audit.statistics()['failed'], 1)
        self.assertEqual(self.service.audit.statistics()['complete'], 0)
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

        stats = self.service.audit.statistics()
        self.assertEqual((stats['complete'], stats['total'], stats['success_rate']), (2, 2, 100))
        self.assertIsNotNone(stats['average_duration_ms'])

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
        self.assertEqual((await self.client.get('/credential-import.js')).status_code, 404)
        for name in ['core.js', 'admin.js', 'test.js']:
            script = await self.client.get('/' + name)
            self.assertEqual(script.status_code, 200)
            self.assertIn('javascript', script.headers['content-type'])
            self.assertEqual(script.headers['x-content-type-options'], 'nosniff')

    def test_console_scripts_reference_existing_elements_and_exports(self):
        import re
        static = Path(__file__).resolve().parents[1] / 'gateway' / 'static'
        ids = set(re.findall(r'\bid="([^"]+)"', (static / 'index.html').read_text()))
        core = (static / 'core.js').read_text()
        exports = set(re.findall(r'^export (?:async )?(?:function|const|class) ([\w$]+)', core, re.M))
        for name in ['admin.js', 'test.js']:
            source = (static / name).read_text()
            referenced = set(re.findall(r"\$\('([A-Za-z]\w*)'\)", source))
            self.assertFalse(referenced - ids, name)
            imported = re.search(r"import \{([^}]+)\} from './core\.js'", source).group(1)
            self.assertFalse({item.strip() for item in imported.split(',')} - exports, name)
            self.assertIn('<script type="module" src="/' + name + '"></script>', (static / 'index.html').read_text())

    async def test_request_logs_filter_by_model(self):
        for model, outcome in [('deepseek-pro', 'complete'), ('deepseek-pro', 'failed'), ('qwen', 'complete')]:
            self.service.audit.add('request', {'time': time.time(), 'method': 'POST', 'path': '/v1/chat/completions', 'category': 'api',
                                               'model': model, 'outcome': outcome, 'http_status': 200, 'duration_ms': 1})
        result = (await self.client.get('/v1/admin/logs?category=api&model=deepseek-pro')).json()
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['models'], ['deepseek-pro', 'qwen'])
        self.assertIn('server_time', result)
        narrowed = (await self.client.get('/v1/admin/logs?category=api&model=deepseek-pro&outcome=failed')).json()
        self.assertEqual(narrowed['total'], 1)
        self.assertEqual((await self.client.get('/v1/admin/logs?model=' + 'x' * 121)).status_code, 422)

    def test_request_categories(self):
        from gateway.audit import request_category
        for path in ['/v1/models', '/v1/capabilities', '/v1/files', '/v1/files/{file_id}', '/v1/files/abc',
                     '/v1/chat/completions', '/v1/conversations/{conversation_id}', '/v1/conversations/delete']:
            self.assertEqual(request_category(path), 'api', path)
        for path in ['<unmatched>', '/', '/status', '/admin.js', '/favicon.ico', '/healthz', '/readyz', '/v1/admin/status',
                     '/v1/admin/probe', '/v1/credentials/status', '/v1/credentials/import', '/v1/credentials/logout']:
            self.assertEqual(request_category(path), 'management', path)


    async def test_monitoring_does_not_fill_history_or_statistics(self):
        for path in ['/v1/admin/status', '/v1/admin/logs', '/v1/admin/login-history', '/healthz']:
            self.assertEqual((await self.client.get(path)).status_code, 200)
        self.assertEqual(self.service.audit.rows('request')['total'], 0)
        self.assertEqual((await self.client.get('/v1/admin/status?window=7d')).json()['statistics']['window'], '7d')
        self.assertEqual((await self.client.get('/v1/admin/status?window=bad')).status_code, 422)
        await self.client.post('/v1/chat/completions', headers={'Authorization':'Bearer wrong'}, json={})
        self.assertEqual(self.service.audit.statistics()['total'], 0)
        self.assertEqual(self.service.audit.rows('request')['items'][0]['http_status'], 401)
        await self.client.post('/v1/chat/completions', json={'model':'does-not-exist','messages':[]})
        self.assertEqual(self.service.audit.statistics()['failed'], 1)

    async def test_automatic_probe_cache_and_transition_logging(self):
        first = await self.client.post('/v1/admin/probe?automatic=true')
        second = await self.client.post('/v1/admin/probe?automatic=true')
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(self.upstream_calls), 5)
        self.assertEqual(self.service.audit.rows('request')['total'], 0)
        self.assertEqual(self.service.audit.rows('login')['total'], 1)
        self.service.last_probe['checked_at'] -= 61
        await self.client.post('/v1/admin/probe?automatic=true')
        self.assertEqual(len(self.upstream_calls), 10)
        self.assertEqual(self.service.audit.rows('login')['total'], 1)
        await self.client.post('/v1/admin/probe')
        self.assertEqual(len(self.upstream_calls), 15)
        self.assertEqual(self.service.audit.rows('login')['total'], 2)
        self.assertEqual(self.service.audit.rows('request')['total'], 1)
        self.service.last_probe['checked_at'] -= 61
        async def denied(**kwargs): raise GatewayError(503,'upstream_authentication_required','Renew')
        with patch.object(self.service, 'site_config', denied):
            await self.client.post('/v1/admin/probe?automatic=true')
        self.assertEqual(self.service.audit.rows('login')['total'], 3)
        self.assertEqual(self.service.audit.rows('login')['items'][0]['result'], 'failed')

    async def test_active_and_waiting_counts_recover_after_cancellation(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def running():
            async with self.service.slot():
                entered.set()
                await release.wait()
        first = asyncio.create_task(running())
        await entered.wait()
        second = asyncio.create_task(running())
        await asyncio.sleep(0)
        state = snapshot(self.service)['gateway']
        self.assertEqual((state['active_requests'], state['queued_requests']), (1, 1))
        second.cancel()
        with self.assertRaises(asyncio.CancelledError): await second
        self.assertEqual(snapshot(self.service)['gateway']['queued_requests'], 0)
        release.set()
        await first
        self.assertEqual((self.service.pending, self.service.running), (0, 0))

    async def test_cancelled_model_count_and_active_log_filter(self):
        from types import SimpleNamespace
        async def downstream(scope, receive, send):
            scope['state']['authorized'] = True
            scope['route'] = SimpleNamespace(path='/v1/chat/completions')
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'partial','more_body':True})
            active = (await self.client.get('/v1/admin/logs?category=api')).json()['active']
            self.assertEqual(len(active), 1)
            self.assertEqual((await self.client.get('/v1/admin/logs?category=management')).json()['active'], [])
            self.assertEqual((await self.client.get('/v1/admin/logs?outcome=failed')).json()['active'], [])
            await receive()
        async def receive(): return {'type':'http.disconnect'}
        async def send(message): pass
        await AuditMiddleware(downstream)({'type':'http','method':'POST','path':'/v1/chat/completions','app':app}, receive, send)
        self.assertEqual(self.service.audit.statistics()['cancelled'], 1)
        self.assertEqual(self.service.audit.statistics()['total'], 1)


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


class MetricPersistenceTests(unittest.TestCase):
    def test_final_outcomes_survive_raw_log_limit_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audit.db'
            store = AuditStore(path)
            store.REQUEST_LIMIT = 2
            started = store.statistics()['started_at']
            self.assertIsNone(store.statistics()['success_rate'])
            self.assertIsNone(store.statistics()['average_duration_ms'])
            for outcome, duration in [('complete', 100), ('failed', 10000), ('cancelled', 9000), ('complete', 300)]:
                store.add('request', {'outcome':outcome, 'duration_ms':duration}, model_request=True)
            store.add('request', {'outcome':'complete', 'duration_ms':1})
            self.assertEqual(store.rows('request')['total'], 2)
            store.close()
            second = AuditStore(path)
            stats = second.statistics()
            self.assertEqual((stats['complete'],stats['failed'],stats['cancelled'],stats['total']), (2,1,1,4))
            self.assertEqual(stats['success_rate'], 50)
            self.assertEqual(stats['average_duration_ms'], 200)
            self.assertEqual(stats['started_at'], started)
            self.assertFalse(stats['complete_window'])
            second.close()

    def test_windows_and_expiration_use_completion_time(self):
        now = 2000000040
        with patch('gateway.audit.time.time', return_value=now-9*86400):
            store = AuditStore(None)
        for offset in [8*86400, 2*86400, 60]:
            with patch('gateway.audit.time.time', return_value=now-offset):
                store.add('request', {'outcome':'complete','duration_ms':200}, model_request=True)
        with patch('gateway.audit.time.time', return_value=now):
            self.assertEqual(store.statistics('24h')['total'], 1)
            self.assertEqual(store.statistics('7d')['total'], 2)
            self.assertTrue(store.statistics('7d')['complete_window'])
            store.prune()
            self.assertEqual(store.db.execute('SELECT SUM(complete) FROM model_metrics').fetchone()[0], 2)
        store.available = False
        self.assertEqual(store.statistics(), {'available':False, 'window':'24h'})
        store.close()
