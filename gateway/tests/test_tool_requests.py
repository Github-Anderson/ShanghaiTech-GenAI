import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from gateway.app import app
from gateway.service import Service
from gateway.settings import Settings
from gateway.tool_bridge import ToolBridge


KEY = 'synthetic-gateway-key-' * 2
MODELS = {'deepseek-pro': {'root_ai_type': 'xinference', 'max_tokens': 4096}}
TOOLS = [{'type': 'function', 'function': {'name': 'read', 'parameters': {
    'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}]


class ToolRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        key_file = Mock(spec=Path)
        key_file.stat.return_value.st_size = len(KEY)
        key_file.read_text.return_value = KEY
        self.service = Service(Settings(key_file, MODELS), None)
        self.generated = []

        async def events(payload, *args, **kwargs):
            self.generated.append(payload)
            yield {'content': 'OK'}
            yield {'finish_reason': 'stop'}

        self.events_patch = patch.object(self.service, 'events', events)
        self.state_patch = patch.object(app.state, 'service', self.service, create=True)
        self.events_patch.start()
        self.state_patch.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url='http://test', headers={'Authorization': 'Bearer ' + KEY})

    async def asyncTearDown(self):
        await self.client.aclose()
        self.state_patch.stop()
        self.events_patch.stop()
        self.service.audit.close()

    async def rejected(self, **fields):
        request = {'model': 'deepseek-pro', 'messages': [{'role': 'user', 'content': 'Read a file'}], **fields}
        response = await self.client.post('/v1/chat/completions', json=request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'invalid_tool_request')
        self.assertEqual(self.generated, [])

    async def test_invalid_tool_containers_and_entries(self):
        for tools in [1, True, 'read', {}, {'type': 'function'}, [None], [1], [{'function': None}]]:
            with self.subTest(tools=tools):
                await self.rejected(tools=tools)

    async def test_invalid_message_containers(self):
        for messages in [None, 1, True, 'message', {}, [None]]:
            with self.subTest(messages=messages):
                await self.rejected(messages=messages)

    async def test_invalid_historical_calls(self):
        for calls in [1, True, 'call', {}, [None], [1], [{'function': None}], [{'function': []}]]:
            with self.subTest(calls=calls):
                await self.rejected(messages=[
                    {'role': 'assistant', 'tool_calls': calls},
                    {'role': 'user', 'content': 'Continue'}])

    async def test_invalid_named_tool_choice(self):
        for name in [None, [], {}, 1, True]:
            with self.subTest(name=name):
                await self.rejected(tools=TOOLS, tool_choice={'type': 'function', 'function': {'name': name}})

    async def test_optional_empty_tools_and_history_remain_supported(self):
        for tools in [None, []]:
            for calls in [None, []]:
                with self.subTest(tools=tools, calls=calls):
                    response = await self.client.post('/v1/chat/completions', json={
                        'model': 'deepseek-pro', 'tools': tools, 'messages': [
                            {'role': 'assistant', 'content': 'Earlier answer', 'tool_calls': calls},
                            {'role': 'user', 'content': 'Continue'}]})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['choices'][0]['message']['content'], 'OK')
                    self.assertEqual(self.generated[-1]['messages'], [
                        {'role': 'assistant', 'content': 'Earlier answer'}])

    def test_historical_arguments_preserve_object_and_json_string_values(self):
        arguments = {'path': 'folder/file.txt'}
        for value in [arguments, json.dumps(arguments)]:
            with self.subTest(value=value):
                request = {'model': 'deepseek-pro', 'messages': [
                    {'role': 'assistant', 'tool_calls': [{'id': 'call-old', 'type': 'function',
                        'function': {'name': 'read', 'arguments': value}}]},
                    {'role': 'tool', 'tool_call_id': 'call-old', 'content': 'File contents'}]}
                rewritten = ToolBridge(request).rewrite(request)
                self.assertEqual(json.loads(rewritten['messages'][0]['content']), {
                    'type': 'tool_calls', 'tool_calls': [
                        {'name': 'read', 'arguments': arguments, 'call_id': 'call-old'}]})


if __name__ == '__main__':
    unittest.main()
