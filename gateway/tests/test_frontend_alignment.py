"""Synthetic regressions for the captured website protocol; no live requests."""
import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from gateway.app import Service, app
from gateway.attachments import Attachment, Attachments, DOCUMENT_MIMES, MAX_IMAGE_BYTES, compress_image
from gateway.conversations import Conversations
from gateway.protocol import GatewayError, build_upstream_payload, trim_history
from gateway.settings import Settings
from gateway.site_api import SigningCredentials, signed_params
from gateway.site_config import SiteCatalog, parse_config
from gateway.upstream import stream_upstream


def records():
    return {'success': True, 'code': 200, 'result': {'records': [
        {'aiType': name, 'aiName': name, 'rootAiType': root, 'status': status,
         'enableDeepThink': thinking, 'maxToken': 1000000}
        for name, root, status, thinking in [
            ('deepseek-pro', 'xinference', 3, 1), ('chatglm', 'xinference', 1, 1),
            ('qwen-instruct', 'xinference', 1, 0), ('GPT-6.1-Sol', 'azure', 3, 1),
            ('image-generator', 'xinference', 2, 0)]]}}


def settings_response(rounds=30):
    return {'success': True, 'code': 200, 'result': {'maxTokens': 16384, 'messageRound': rounds}}


def dictionary():
    return {'success': True, 'code': 200, 'result': [{'value': 'https://genaipic.shanghaitech.edu.cn/'}]}


MODELS = parse_config(records(), settings_response(), dictionary()).models


class Credentials:
    token = 'synthetic-token'

    def get_token(self):
        return self.token

    def get_signing(self, token):
        return SigningCredentials('synthetic-salt', 'synthetic-tenant')


def frame(value):
    return ('data: ' + (value if isinstance(value, str) else json.dumps(value)) + '\n\n').encode()


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_catalog_capabilities_and_disabled_models(self):
        config = parse_config(records(), settings_response(), dictionary(), {'chatglm': {'enabled': False}})
        self.assertEqual(set(config.models), {'deepseek-pro', 'qwen-instruct', 'GPT-6.1-Sol'})
        self.assertTrue(all(m['supports_search'] and m['supports_files'] for m in config.models.values()))
        self.assertTrue(config.models['deepseek-pro']['supports_thinking'])
        self.assertFalse(config.models['qwen-instruct']['supports_thinking'])
        self.assertEqual(config.models['GPT-6.1-Sol']['root_ai_type'], 'azure')
        self.assertTrue(all(m['max_tokens'] == 16384 for m in config.models.values()))
        self.assertFalse(config.models['qwen-instruct']['supports_images'])
        for rounds in (-1, True, '30'):
            with self.assertRaises(GatewayError):
                parse_config(records(), settings_response(rounds), dictionary())

    async def test_explicit_refresh_updates_names_and_order_without_changing_routing(self):
        old = records()
        kimi = {'aiType': 'Kimi-k3', 'aiName': 'Kimi-K3', 'modelCode': 'Kimi-k3',
                'rootAiType': 'xinference', 'status': 3, 'enableDeepThink': 1}
        old['result']['records'].append(kimi)
        current = copy.deepcopy(old)
        calls = []
        def respond(request):
            calls.append(request.url.path)
            value = current if request.url.path.endswith('/list') else settings_response() if request.url.path.endswith('/detail') else dictionary()
            return httpx.Response(200, json=value)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            catalog = SiteCatalog(client, Credentials())
            before = await catalog.get()
            moved = current['result']['records'].pop()
            moved.update(aiName='Kimi K3.1 Pro', modelCode='Kimi K3.1 Pro', orderSort=11)
            current['result']['records'].insert(1, moved)
            self.assertIs(await catalog.get(), before)
            self.assertEqual(len(calls), 3)
            after = await catalog.get(refresh=True)
            self.assertEqual(len(calls), 6)
            self.assertEqual(list(after.models)[1], 'Kimi-k3')
            self.assertEqual(after.models['Kimi-k3']['display_name'], 'Kimi K3.1 Pro')
            payload, _ = build_upstream_payload({'model': 'Kimi-k3', 'messages': [{'role': 'user', 'content': 'synthetic'}]}, after.models)
            self.assertEqual(payload['aiType'], 'Kimi-k3')
            self.assertEqual(after.message_round, before.message_round)
            self.assertEqual(after.image_base, before.image_base)
            self.assertEqual(len(calls), 6, 'No background polling is scheduled')

    async def test_atomic_cache_expiry_signature_and_credential_rotation(self):
        calls, broken = [], False
        credentials = Credentials()
        def respond(request):
            calls.append(request)
            params = dict(request.url.params)
            params.pop('_t', None)
            _, sign = signed_params(request.url.path, params, 'synthetic-salt')
            self.assertEqual(request.headers['X-Sign'], sign)
            self.assertEqual(request.headers['tenant-id'], 'synthetic-tenant')
            if request.url.path.endswith('/list'):
                self.assertEqual(params, {'pageNo': '1', 'pageSize': '999', 'showStatusList': '2,3'})
                data = records()
            elif request.url.path.endswith('/detail'):
                data = {} if broken else settings_response()
            else:
                data = dictionary()
            return httpx.Response(200, json=data)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            catalog = SiteCatalog(client, credentials)
            first, same = await asyncio.gather(catalog.get(), catalog.get())
            self.assertIs(first, same)
            self.assertEqual(len(calls), 3)
            credentials.token = 'rotated-synthetic-token'
            second = await catalog.get()
            self.assertEqual(len(calls), 6)
            with self.assertRaises(GatewayError):
                first.check_token(credentials.token)
            second.check_token(credentials.token)
            broken, catalog.expires = True, 0
            with self.assertRaises(GatewayError):
                await catalog.get()
            self.assertIs(catalog.value, second, 'Failed refresh must not publish a partial snapshot')
            with self.assertRaises(GatewayError):
                await catalog.get()

    async def test_http_catalog_requires_auth_and_reads_website(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'gateway.key'
            key.write_text('synthetic-key-' * 4)
            settings = Settings(key, dynamic_site_config=True)
            def respond(request):
                data = records() if request.url.path.endswith('/list') else settings_response() if request.url.path.endswith('/detail') else dictionary()
                return httpx.Response(200, json=data)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as site:
                service = Service(settings, site)
                service.catalog.credentials = Credentials()
                with patch.object(app.state, 'service', service, create=True):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                        self.assertEqual((await client.get('/v1/models')).status_code, 401)
                        headers = {'Authorization': 'Bearer ' + key.read_text()}
                        models = (await client.get('/v1/models', headers=headers)).json()['data']
                        caps = (await client.get('/v1/capabilities', headers=headers)).json()
                self.assertEqual(len(models), 4)
                self.assertEqual(caps['limits']['max_history_messages'], 60)
                self.assertEqual(caps['files']['max_image_bytes'], 10 * 1024 * 1024)
                self.assertEqual(caps['options']['sources_phase'], 'stream_metadata')


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_tail_sources_and_azure_metadata_do_not_end_answer(self):
        sources = {'count': 20, 'links': [{'num': i, 'title': str(i), 'link': f'https://example.invalid/{i}', 'snippet': 'evidence'} for i in range(1, 21)]}
        values = [ {'stage': 'searching'}, {'stage': 'searched', 'count': 20},
            {'other': json.dumps({'contentId': 'early-metadata'})},
            {'choices': [{'delta': {'reasoning_content': 'reason', 'content': 'answer'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}, '[DONE]',
            {'usage': {'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13}},
            {'other': json.dumps({'netResults': sources, 'promptTokens': 3910,
                                 'totalTokens': 5000, 'answerGroupId': 'synthetic-answer'})}]
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                for index, value in enumerate(values):
                    if index == len(values)-1:
                        await asyncio.sleep(1.05)  # Trailer can arrive later than the old one-second cutoff.
                    yield frame(value)
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Body())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            events = [e async for e in stream_upstream(client, {}, 'azure', 'synthetic-token')]
        self.assertEqual(events[0], {'search_progress': {'stage': 'searching'}})
        self.assertEqual(''.join(e.get('content', '') for e in events), 'answer')
        self.assertEqual(''.join(e.get('reasoning_content', '') for e in events), 'reason')
        self.assertEqual(events[-1], {'finish_reason': 'stop'})
        metadata = events[-2]
        self.assertEqual(metadata['sources'], sources)
        self.assertEqual(metadata['usage']['prompt_tokens'], 10)
        self.assertEqual(metadata['upstream_metadata']['promptTokens'], 3910)
        self.assertEqual(metadata['upstream_metadata']['answerGroupId'], 'synthetic-answer')
        self.assertEqual(metadata['upstream_metadata']['contentId'], 'early-metadata')
        self.assertEqual(len(calls), 1, 'Sources come from the chat stream, without an extra search request')

    async def test_metadata_only_and_source_failure(self):
        async def replay(values):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
                    headers={'content-type': 'text/event-stream'}, content=b''.join(map(frame, values))))) as client:
                return [e async for e in stream_upstream(client, {}, 'azure', 'synthetic-token')]
        with self.assertRaises(GatewayError) as error:
            await replay([{'other': {'promptTokens': 100}}])
        self.assertEqual(error.exception.code, 'upstream_incomplete')
        events = await replay([{'choices': [{'delta': {'content': 'answer'}, 'finish_reason': 'stop'}]},
                              '[DONE]', {'other': {'netResults': {'links': 'bad'}}}])
        self.assertEqual(events[-1]['finish_reason'], 'stop')
        self.assertEqual(events[-2]['sources_status'], 'failed')


class HistoryAndAttachmentTests(unittest.IsolatedAsyncioTestCase):
    def test_round_window_preserves_system_and_current(self):
        history = [{'role': role, 'content': f'{n}-{role}'} for n in range(32) for role in ('user', 'assistant')]
        request = {'model': 'deepseek-pro', 'messages': [{'role': 'system', 'content': 'system'}, *history, {'role': 'user', 'content': 'current'}]}
        payload, _ = build_upstream_payload(request, MODELS)
        self.assertEqual(len(payload['messages']), 61)
        self.assertEqual(payload['messages'][0]['content'], 'system')
        self.assertEqual(payload['messages'][1]['content'], '2-user')
        self.assertEqual(payload['chatInfo'], 'current')
        self.assertEqual(len(trim_history(request, 0)['messages']), 66)
        self.assertEqual(len(request['messages']), 66)

    async def test_prompt_tokens_follow_group_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / 'conversations.json'
            service = Service(Settings(Path('unused'), MODELS), None)
            service.credentials = Credentials()
            service.conversations = Conversations(journal)
            sent = []
            async def generate(payload, *args, **kwargs):
                sent.append(copy.deepcopy(payload))
                yield {'content': 'answer'}
                yield {'upstream_metadata': {'promptTokens': 3900 + len(sent)}}
                yield {'finish_reason': 'stop'}
            service.generate = generate
            async def turn(local='one', model='deepseek-pro'):
                return [e async for e in service.events({'aiType': model, 'chatGroupId': 'unused'},
                    'xinference', public_model=model, conversation_id=local)]
            await turn()
            await turn()
            self.assertEqual([p['promptTokens'] for p in sent], [0, 3901])
            service.conversations = Conversations(journal)
            await turn()
            await turn(model='chatglm')
            await turn(local='new')
            self.assertEqual([p['promptTokens'] for p in sent], [0, 3901, 3902, 0, 0])

    def test_image_compression_and_limits(self):
        output = io.BytesIO()
        Image.effect_noise((3000, 1200), 100).convert('RGB').save(output, format='PNG')
        original = output.getvalue()
        name, mime, compressed = compress_image('large.png', 'image/png', original)
        self.assertEqual(mime, 'image/jpeg')
        self.assertLess(len(compressed), len(original))
        self.assertEqual(Image.open(io.BytesIO(compressed)).size, (2048, 819))
        self.assertEqual(compress_image('a.gif', 'image/gif', b'GIF89a-data')[2], b'GIF89a-data')
        small = io.BytesIO()
        Image.new('RGB', (10, 10)).save(small, format='PNG')
        self.assertEqual(compress_image('small.png', 'image/png', small.getvalue())[2], small.getvalue())
        with self.assertRaises(GatewayError):
            Attachments.validate_file('large.png', 'image/png', b'\x89PNG\r\n\x1a\n' + b'x' * (MAX_IMAGE_BYTES-8), 'deepseek-pro', MODELS)
        for ext in ('.md', '.markdown', '.log', '.htm'):
            self.assertEqual(Attachments.validate_file('doc'+ext, DOCUMENT_MIMES[ext], b'plain text', 'qwen-instruct', MODELS)[0], 'document')

    async def test_current_documents_only_and_history_images_retained(self):
        store = Attachments()
        fingerprint = hashlib.sha256(b'synthetic-token').hexdigest()
        for name, kind in [('old-doc', 'document'), ('new-doc', 'document'), ('old-image', 'image')]:
            store.records[name] = Attachment(name, 'deepseek-pro', kind, 'site-'+name, name+'.txt', 10,
                int(time.time()), group=name+'-group', credential_fingerprint=fingerprint)
        def user(*names):
            return {'role': 'user', 'content': [{'type': 'text', 'text': 'question'},
                *[{'type': 'file', 'file': {'file_id': name}} for name in names]]}
        request = {'model': 'deepseek-pro', 'messages': [user('old-doc', 'old-image'),
            {'role': 'assistant', 'content': 'answer'}, user('new-doc')]}
        rewritten, plan = store.plan(request, MODELS)
        payload, _ = build_upstream_payload(rewritten, MODELS)
        payload = await store.materialize(None, MODELS, 'deepseek-pro', plan, payload, 'synthetic-token', bound_group='new-doc-group')
        self.assertEqual(payload['fileIds'], ['site-new-doc'])
        self.assertIn('old-doc.txt', payload['messages'][0]['content'][0]['text'])
        self.assertEqual(payload['messages'][0]['content'][1]['image_url']['url'], 'site-old-image')
        self.assertEqual(payload['imageUrls'], [])


if __name__ == '__main__':
    unittest.main()
