"""Regressions for image turns following failed/timeout generations."""
import asyncio
import hashlib
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from gateway.app import Service
from gateway.attachments import Attachment, Attachments, STATIC_PREFIX
from gateway.protocol import GatewayError, build_upstream_payload
from gateway.settings import Settings
from gateway.tool_bridge import ToolBridge
from gateway.upstream import stream_upstream


MODELS = {'deepseek-pro': {'root_ai_type': 'xinference', 'max_tokens': 4096,
                           'supports_images': True}}
TOOLS = [{'type': 'function', 'function': {'name': 'read', 'parameters': {
    'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}]


class ImageTurnTests(unittest.IsolatedAsyncioTestCase):
    async def payload(self, current_image=True):
        store = Attachments()
        for name in ['old', 'new']:
            store.records[name] = Attachment(
                id=name, model='deepseek-pro', kind='image', value=STATIC_PREFIX + name + '.png',
                filename=name + '.png', size=10, created=int(time.time()), width=500, height=280,
                credential_fingerprint=hashlib.sha256(b'synthetic-token').hexdigest())
        def question(image):
            parts = [{'type': 'text', 'text': '这是什么'}]
            if image:
                parts.append({'type': 'file', 'file': {'file_id': image}})
            return {'role': 'user', 'content': parts}
        # No assistant response between the two human messages: first timed out.
        data = {'model': 'deepseek-pro', 'tools': TOOLS, 'messages': [
            {'role': 'system', 'content': 'Use client tools.'}, question('old'),
            {'role': 'developer', 'content': 'Trusted runtime context.'},
            question('new' if current_image else None)]}
        rewritten = ToolBridge(data).rewrite(data)
        users = [m for m in rewritten['messages'] if m['role'] == 'user']
        self.assertEqual(len(users), 2, 'Distinct human turns must survive a missing answer')
        self.assertNotIn('GATEWAY TOOL PROTOCOL', str(users[0]['content']))
        rewritten, plan = store.plan(rewritten, MODELS)
        payload, _ = build_upstream_payload(rewritten, MODELS)
        return await store.materialize(None, MODELS, 'deepseek-pro', plan, payload, 'synthetic-token')

    async def test_new_image_is_current_and_failed_image_is_history(self):
        payload = await self.payload()
        self.assertEqual(payload['imageUrls'], [STATIC_PREFIX + 'new.png'])
        self.assertEqual(payload['imageUrl'], STATIC_PREFIX + 'new.png')
        self.assertEqual(payload['messages'][-1]['content'][1]['image_url']['url'], STATIC_PREFIX + 'old.png')
        self.assertEqual(payload['messages'][-1]['content'][0]['text'], '这是什么')
        self.assertTrue(payload['chatInfo'].startswith('这是什么'))

    async def test_text_followup_does_not_resend_old_image_as_current(self):
        payload = await self.payload(current_image=False)
        self.assertEqual(payload['imageUrls'], [])
        self.assertEqual(payload['imageUrl'], '')
        self.assertEqual(payload['messages'][-1]['content'][1]['image_url']['url'], STATIC_PREFIX + 'old.png')

    async def test_image_header_wait_uses_total_budget_but_text_keeps_idle_limit(self):
        settings = Settings(Path('unused'), MODELS, read_timeout_seconds=0.01, total_timeout_seconds=0.1)
        observed = []
        def respond(request):
            observed.append(request.extensions['timeout'])
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content='data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), timeout=0.01) as client:
            service = Service(settings, client)
            for images in [[], [STATIC_PREFIX + 'new.png']]:
                events = [event async for event in service.generation_stream(
                    {'aiType': 'deepseek-pro', 'imageUrls': images}, 'xinference', 'synthetic-token')]
                self.assertTrue(any(event.get('finish_reason') for event in events))
        self.assertEqual([item['read'] for item in observed], [0.01, 0.1])
        self.assertEqual([item['connect'] for item in observed], [0.01, 0.01])

    async def test_image_stream_still_has_idle_timeout_and_no_retry(self):
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(0.1)
                yield b'data: [DONE]\n\n'
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=SlowBody())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(GatewayError) as error:
                async for _ in stream_upstream(client, {}, 'xinference', 'synthetic-token',
                                                idle_timeout=0.01, header_timeout=0.2):
                    pass
        self.assertEqual(error.exception.code, 'upstream_timeout')
        self.assertEqual(len(calls), 1)

    async def test_absolute_total_deadline_still_cancels_slow_headers(self):
        settings = Settings(Path('unused'), MODELS, read_timeout_seconds=0.01, total_timeout_seconds=0.02)
        calls = []
        async def stalled(*args, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(1)
            yield {'content': 'too late'}
        service = Service(settings, None)
        with patch('gateway.app.stream_upstream', stalled):
            with self.assertRaises(GatewayError) as error:
                async for _ in service.generate({'aiType': 'deepseek-pro', 'imageUrls': ['image']},
                                                 'xinference', 'synthetic-token', None):
                    pass
        self.assertEqual(error.exception.code, 'upstream_timeout')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
