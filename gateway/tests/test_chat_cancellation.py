import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gateway.app import app
from gateway.service import Service
from gateway.settings import Settings

KEY = "synthetic-gateway-key-" * 2
MODELS = {"deepseek-pro": {"root_ai_type": "xinference", "max_tokens": 4096}}


class ChatCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        key_file = Mock(spec=Path)
        key_file.stat.return_value.st_size = len(KEY)
        key_file.read_text.return_value = KEY
        self.service = Service(Settings(key_file, MODELS, total_timeout_seconds=10), None)
        self.service.credentials = SimpleNamespace(get_token=lambda: "synthetic-token")
        self.state_patch = patch.object(app.state, "service", self.service, create=True)
        self.state_patch.start()

    async def asyncTearDown(self):
        self.state_patch.stop()
        self.service.audit.close()

    async def disconnect(self, *, stream, after_first=False):
        existing_tasks = asyncio.all_tasks()
        started, closed, sent_text = asyncio.Event(), asyncio.Event(), asyncio.Event()
        incoming = asyncio.Queue()
        body = json.dumps(
            {
                "model": "deepseek-pro",
                "stream": stream,
                "conversation_id": "cancel-test",
                "messages": [{"role": "user", "content": "Question"}],
            }
        ).encode()
        incoming.put_nowait({"type": "http.request", "body": body, "more_body": False})
        sent = []

        async def upstream(*args, **kwargs):
            try:
                started.set()
                if after_first:
                    yield {"content": "partial"}
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and b"partial" in message.get("body", b""):
                sent_text.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions",
            "query_string": b"",
            "root_path": "",
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
            "headers": [
                (b"authorization", ("Bearer " + KEY).encode()),
                (b"content-type", b"application/json"),
            ],
        }
        try:
            with patch.object(self.service, "generation_stream", upstream):
                request = asyncio.create_task(app(scope, incoming.get, send))
                await asyncio.wait_for(started.wait(), 1)
                self.assertEqual((self.service.pending, self.service.running), (1, 1))
                if after_first:
                    await asyncio.wait_for(sent_text.wait(), 1)
                else:
                    self.assertFalse(sent)
                incoming.put_nowait({"type": "http.disconnect"})
                done, _ = await asyncio.wait({request}, timeout=1)
                self.assertIn(request, done, "Client disconnect did not stop generation")
                if not request.cancelled():
                    request.result()
                self.assertTrue(closed.is_set())
                self.assertEqual((self.service.pending, self.service.running), (0, 0))
                self.assertFalse(
                    any(lock.locked() for lock in self.service.conversations.locks.values())
                )
                self.assertEqual(self.service.audit.active, {})
                self.assertEqual(
                    self.service.audit.rows("request")["items"][0]["outcome"], "cancelled"
                )
                self.assertFalse(any(b"[DONE]" in m.get("body", b"") for m in sent))
                await asyncio.sleep(0)
                self.assertEqual(asyncio.all_tasks() - existing_tasks, set())
        finally:
            remaining = asyncio.all_tasks() - existing_tasks
            for task in remaining:
                task.cancel()
            if remaining:
                await asyncio.gather(*remaining, return_exceptions=True)

    async def test_nonstream_disconnect_before_first_output_releases_generation(self):
        await self.disconnect(stream=False)

    async def test_stream_disconnect_during_prefetch_releases_generation(self):
        await self.disconnect(stream=True)

    async def test_stream_disconnect_after_first_output_releases_generation(self):
        await self.disconnect(stream=True, after_first=True)
