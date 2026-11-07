"""Literal answers must not require JSON escaping for shell/code examples."""
import json
import unittest

from gateway.protocol import GatewayError
from gateway.tool_bridge import ToolBridge
from gateway.tool_encoding import FINAL_TEXT_PREFIX


TOOLS = [{'type': 'function', 'function': {'name': 'bash', 'parameters': {
    'type': 'object', 'properties': {'command': {'type': 'string'}},
    'required': ['command'], 'additionalProperties': False}}}]


class FinalTextTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self, **options):
        return ToolBridge({'tools': TOOLS, **options})

    async def convert(self, raw, size=1, finish='stop', **options):
        async def upstream():
            for i in range(0, len(raw), size):
                yield {'content': raw[i:i + size]}
            yield {'finish_reason': finish}
        return [event async for event in self.bridge(**options).convert(upstream())]

    async def test_session_command_examples_across_stream_boundaries(self):
        answers = [
            '执行 `osascript -e \'quit app "Docker"\'`。',
            '```bash\nioreg -p IOUSB -w0 -l | grep -iE "usb|receiver"\n```',
            '```bash\npmset -g assertions | grep -A1 "USB"\n```',
            '中文 😀\nC:\\temp\\new\nLiteral \\n and \\u0041 stay literal.\n  ',
            '```json\n{"type":"tool_calls","tool_calls":[{"name":"bash",'
            '"arguments":{"command":"must not execute"}}]}\n```',
        ]
        for answer in answers:
            for size in [1, 2, 13, 4096]:
                with self.subTest(answer=answer, size=size):
                    events = await self.convert(' \n' + FINAL_TEXT_PREFIX + answer, size)
                    self.assertEqual(''.join(e.get('content', '') for e in events), answer)
                    self.assertEqual(events[-1], {'finish_reason': 'stop'})
                    self.assertFalse(any('tool_calls' in e for e in events))

    async def test_answer_streams_before_upstream_finishes(self):
        async def upstream():
            yield {'content': FINAL_TEXT_PREFIX + '正文 "USB"'}
            self.assertTrue(seen, 'Answer should arrive before finish')
            yield {'finish_reason': 'stop'}
        seen = []
        async for event in self.bridge().convert(upstream()):
            seen.append(event)
        self.assertEqual(seen[0], {'content': '正文 "USB"'})

    async def test_legacy_json_final_remains_supported(self):
        answer = '```bash\ngrep "USB"\n```'
        events = await self.convert(json.dumps({'type': 'final', 'content': answer}))
        self.assertEqual(''.join(e.get('content', '') for e in events), answer)

    async def test_tool_json_and_schema_validation_unchanged(self):
        raw = json.dumps({'type': 'tool_calls', 'tool_calls': [
            {'name': 'bash', 'arguments': {'command': 'printf "hello"'}}]})
        events = await self.convert(raw)
        self.assertEqual(events[-1], {'finish_reason': 'tool_calls'})
        self.assertEqual(json.loads(events[0]['tool_calls'][0]['function']['arguments']),
                         {'command': 'printf "hello"'})
        with self.assertRaises(GatewayError) as caught:
            await self.convert(raw.replace('"command"', '"unknown"'))
        self.assertEqual(caught.exception.code, 'tool_arguments_invalid')
        # Broken tool JSON must never receive the literal-answer treatment.
        with self.assertRaises(GatewayError):
            await self.convert(raw[:-1])

    async def test_empty_required_and_truncated_answers_fail(self):
        cases = [('', {}, 'upstream_empty_response'),
                 ('answer', {'tool_choice': 'required'}, 'tool_choice_not_satisfied'),
                 ('answer', {'tool_choice': {'type': 'function', 'function': {'name': 'bash'}}},
                  'tool_choice_not_satisfied')]
        for answer, options, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(GatewayError) as caught:
                    await self.convert(FINAL_TEXT_PREFIX + answer, **options)
                self.assertEqual(caught.exception.code, code)
        with self.assertRaises(GatewayError) as caught:
            await self.convert(FINAL_TEXT_PREFIX + 'partial answer', finish='length')
        self.assertEqual(caught.exception.code, 'tool_bridge_incomplete')

    def test_rewritten_protocol_requests_literal_answers(self):
        data = {'model': 'deepseek-pro', 'tools': TOOLS,
                'messages': [{'role': 'user', 'content': 'Explain the output'}]}
        rewritten = self.bridge().rewrite(data)
        self.assertIn('start with GATEWAY_FINAL on its own line', rewritten['messages'][-1]['content'])


if __name__ == '__main__':
    unittest.main()
