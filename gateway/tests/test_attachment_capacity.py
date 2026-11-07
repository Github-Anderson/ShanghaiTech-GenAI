import hashlib
import time
import unittest
from unittest.mock import AsyncMock

from gateway.attachments import Attachment, Attachments, MAX_FILES
from gateway.protocol import GatewayError


class AttachmentCapacityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Attachments()
        self.models = {"model": {"supports_files": True}}
        self.data = b"document content"
        self.token = "synthetic-token"
        self.group = "g" * 21
        self.client = AsyncMock()
        for index in range(MAX_FILES):
            record = Attachment(
                id=f"file-{index}", model="model", kind="document",
                value=f"remote-{index}", filename="document.txt",
                size=len(self.data), created=int(time.time()), group=self.group,
                sha256=hashlib.sha256(self.data).hexdigest() if index == 0 else str(index),
                mime_type="text/plain", parsed_content="document content",
                credential_fingerprint=hashlib.sha256(self.token.encode()).hexdigest(),
            )
            self.store.records[record.id] = record

    async def upload(self, **overrides):
        values = {"model": "model", "data": self.data, "token": self.token,
                  "chat_group_id": self.group, "mime": "text/plain"}
        values.update(overrides)
        return await self.store.upload(
            self.client, self.models, values.pop("model"), "document.txt",
            values.pop("mime"), values.pop("data"), values.pop("token"), **values,
        )

    async def test_duplicate_document_reuses_reference_at_capacity(self):
        record = await self.upload()
        self.assertIs(record, self.store.records["file-0"])
        self.assertEqual(len(self.store.records), MAX_FILES)
        self.client.stream.assert_not_called()

    async def test_distinct_documents_still_obey_capacity(self):
        self.models["other"] = {"supports_files": True}
        for changes in ({"data": b"new document"}, {"token": "other-token"},
                        {"chat_group_id": "h" * 21}, {"model": "other"},
                        {"mime": "application/octet-stream"}):
            with self.subTest(changes=changes), self.assertRaises(GatewayError) as caught:
                await self.upload(**changes)
            self.assertEqual(caught.exception.code, "attachment_capacity")
        self.assertEqual(len(self.store.records), MAX_FILES)
        self.client.stream.assert_not_called()
