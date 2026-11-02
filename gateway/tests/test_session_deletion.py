"""Offline deletion contract and persistence tests. Never use real credentials."""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from gateway.app import app, Service, delete_conversations
from gateway.conversations import Conversations, TTL_SECONDS
from gateway.protocol import GatewayError
from gateway.settings import Settings

TOKEN='synthetic-token-never-live'
MODELS={'deepseek-pro':{'root_ai_type':'xinference','max_tokens':4096}}

class SessionDeletionTests(unittest.IsolatedAsyncioTestCase):
    def service(self, client, directory):
        settings=Settings(Path(directory)/'key',MODELS,
                          imported_credentials_file=Path(directory)/'credentials.json')
        settings.gateway_key_file.write_text('synthetic-local-key-'+'x'*32)
        service=Service(settings,client)
        service.credentials=SimpleNamespace(get_token=lambda:TOKEN,get_tenant=lambda token:'0')
        return service

    async def test_multiple_models_expired_groups_restart_and_idempotency(self):
        calls=[]
        def respond(request):
            calls.append(request)
            self.assertEqual(request.method,'GET')
            self.assertEqual(request.url.path,'/htk/ai/history/delete/groupId')
            self.assertEqual(request.headers['x-access-token'],TOKEN)
            self.assertEqual(request.headers['tenant-id'],'0')
            self.assertNotIn('x-sign',request.headers)
            self.assertIn('_t',request.url.params)
            return httpx.Response(200,json={'success':True,'code':200,'result':None})
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                service=self.service(client,directory)
                one=service.conversations.group_for(TOKEN,'one','session')
                with patch('gateway.conversations.time.time',return_value=time.time()+TTL_SECONDS+1):
                    two=service.conversations.group_for(TOKEN,'one','session')
                three=service.conversations.group_for(TOKEN,'two','session')
                other=service.conversations.group_for(TOKEN,'one','unrelated')
                service.conversations=Conversations(service.conversations.path)
                record=SimpleNamespace(group=one)
                service.attachments.records['file-test']=record
                # Avoid Attachment TTL logic in this synthetic record.
                service.attachments.delete=lambda ref:service.attachments.records.pop(ref)
                result=await delete_conversations(service,['session'])
                self.assertTrue(result['deleted'])
                self.assertEqual(result['deleted_groups'],3)
                self.assertEqual({r.url.params['id'] for r in calls},{one,two,three})
                self.assertNotIn('file-test',service.attachments.records)
                self.assertIn(other,service.conversations.history)
                service.conversations=Conversations(service.conversations.path)
                self.assertEqual((await delete_conversations(service,['session']))['deleted_groups'],0)
                self.assertEqual(len(calls),3)
                journal=service.conversations.path.read_text()
                self.assertNotIn(TOKEN,journal)
                fresh=service.conversations.group_for(TOKEN,'one','session')
                self.assertNotIn(fresh,{one,two,three})

    async def test_partial_business_failure_preserves_ledger(self):
        calls=[]
        def respond(request):
            calls.append(request)
            return httpx.Response(200,json={'success':len(calls)==1,'code':200 if len(calls)==1 else 500})
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                service=self.service(client,directory)
                groups={service.conversations.group_for(TOKEN,m,'s') for m in ('one','two')}
                with self.assertRaises(GatewayError) as error: await delete_conversations(service,['s'])
                self.assertEqual(error.exception.code,'site_business_error')
                self.assertEqual(set(service.conversations.history),groups)
                self.assertFalse(service.deleting)
                self.assertEqual(service.pending,0)

    async def test_auth_redirect_timeout_and_bad_json(self):
        for response,code in [(httpx.Response(401),'upstream_authentication_required'),
                              (httpx.Response(302,headers={'location':'https://example.invalid'}),'site_http_error'),
                              (httpx.Response(200,text='bad',headers={'content-type':'application/json'}),'site_protocol_error'),
                              (httpx.Response(200,json={'success':True,'code':'200'}),'site_business_error')]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:response)) as client:
                    service=self.service(client,directory)
                    group=service.conversations.group_for(TOKEN,'model','s')
                    with self.assertRaises(GatewayError) as error: await delete_conversations(service,['s'])
                    self.assertEqual(error.exception.code,code)
                    self.assertIn(group,service.conversations.history)
        def timeout(request):raise httpx.ReadTimeout('synthetic-secret-in-exception',request=request)
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
                service=self.service(client,directory);service.conversations.group_for(TOKEN,'model','s')
                with self.assertRaises(GatewayError) as error:await delete_conversations(service,['s'])
                self.assertEqual(error.exception.code,'site_delete_timeout')
                self.assertNotIn('synthetic-secret',str(error.exception))

    async def test_unknown_credentials_busy_and_request_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            service=self.service(None,directory)
            group=service.conversations.group_for(TOKEN,'model','s')
            for ids in (['unknown'],['s','unknown']):
                with self.assertRaises(GatewayError) as error:await delete_conversations(service,ids)
                self.assertEqual(error.exception.status_code,404)
            service.credentials.get_token=lambda:'different-synthetic-token'
            with self.assertRaises(GatewayError):await delete_conversations(service,['s'])
            self.assertIn(group,service.conversations.history)
            service.pending=1
            with self.assertRaises(GatewayError) as error:await delete_conversations(service,['s'])
            self.assertEqual(error.exception.status_code,409)
            service.pending=0;service.deleting=True
            with self.assertRaises(GatewayError):
                async with service.slot():self.fail('Deletion gate bypassed')

    async def test_http_routes_require_key_validate_batch_and_never_get_delete(self):
        calls=[]
        def respond(request):calls.append(request);return httpx.Response(200,json={'success':True,'code':200})
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
                service=self.service(upstream,directory)
                app.state.service=service;app.state.init_error=None
                service.conversations.group_for(TOKEN,'model','s')
                headers={'authorization':'Bearer '+service.settings.gateway_key_file.read_text()}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as client:
                    self.assertEqual((await client.delete('/v1/conversations/s')).status_code,401)
                    self.assertEqual((await client.get('/v1/conversations/s',headers=headers)).status_code,405)
                    for value in ({}, {'conversation_ids':[]},{'conversation_ids':[None]}, {'conversation_ids':[{'id':'s'}]}):
                        self.assertEqual((await client.post('/v1/conversations/delete',headers=headers,json=value)).status_code,400)
                    self.assertFalse(calls)
                    result=await client.delete('/v1/conversations/s',headers=headers)
                    self.assertEqual(result.status_code,200)
                    self.assertTrue(result.json()['deleted'])
                    self.assertEqual(result.headers['cache-control'],'no-store')
                    self.assertEqual(len(calls),1)

    async def test_corrupt_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'conversations.json';path.write_text('broken')
            with self.assertRaises(GatewayError):Conversations(path)

if __name__=='__main__':unittest.main()
