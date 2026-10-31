"""Attachment paths/fields from image E265/E270, pdf E265/E268, file E272/E275.

All uploads are triggered by a client action; no network calls on startup.
Uploaded metadata is in memory and expires on restart or after one hour.
"""
from __future__ import annotations

import base64
import asyncio
import binascii
import hashlib
import json
import secrets
import time
import weakref
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx

from .protocol import GatewayError, MAX_INPUT_BYTES, _new_conversation_id
from .site_api import read_site_json

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_FILES = 64
FILE_TTL_SECONDS = 3600
IMAGE_ORIGIN = "https://genaipic.shanghaitech.edu.cn"
STATIC_PREFIX = IMAGE_ORIGIN + "//sys/common/static/"
IMAGE_MIMES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
# Conservative allowlist: successful synthetic uploads, not inferred from labels.
DOCUMENT_MIMES = {'.pdf': 'application/pdf', '.txt': 'text/plain', '.csv': 'text/csv',
                  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def bad(message):
    return GatewayError(400, "invalid_attachment", message)


@dataclass
class Attachment:
    id: str
    model: str
    kind: str
    value: str
    filename: str
    size: int
    created: int
    group: str = ""
    width: int = 0
    height: int = 0
    parsed_content: str = ""
    sha256: str = ""
    mime_type: str = ""
    credential_fingerprint: str = field(default='', repr=False)

    def public(self, include_content=True):
        data = {"id": self.id, "object": "file", "bytes": self.size, "created_at": self.created,
                "filename": self.filename, "purpose": "user_data", "kind": self.kind, "model": self.model,
                "expires_at": self.created + FILE_TTL_SECONDS, "status": "ready",
                "sha256": self.sha256, "mime_type": self.mime_type,
                "persistent": False, "model_bound": True}
        if self.kind == "image":
            data.update({"url": self.value, "width": self.width, "height": self.height})
        elif include_content:
            data["parsed_content"] = self.parsed_content
        return data


class Attachments:
    def __init__(self):
        self.records = {}
        self.image_cache = {}
        self.upload_locks = weakref.WeakValueDictionary()

    def prune(self):
        now = time.time()
        self.records = {key: value for key, value in self.records.items() if now - value.created < FILE_TTL_SECONDS}
        self.image_cache = {key: ref for key, ref in self.image_cache.items() if ref in self.records}

    def get(self, ref, model=None):
        record = self.records.get(ref) if isinstance(ref, str) else None
        expired = record is not None and time.time() - record.created >= FILE_TTL_SECONDS
        self.prune()
        if expired:
            raise GatewayError(404, 'file_expired', 'The attachment expired. Upload the source file again before sending.')
        record = self.records.get(ref) if isinstance(ref, str) else None
        if record is None:
            raise GatewayError(404, 'file_not_found', 'Attachment is unknown, deleted, or expired. Upload the source file again.')
        if model is not None and record.model != model:
            raise GatewayError(400, 'file_model_mismatch', 'Attachments are bound to their upload model. Re-upload for the selected model.')
        return record

    def delete(self, ref):
        """Release a local reference only. No remote deletion or cancellation."""
        self.prune()
        deleted = self.records.pop(ref, None) is not None
        self.image_cache = {key: value for key, value in self.image_cache.items() if value != ref}
        return {'id': ref, 'object': 'file', 'deleted': deleted, 'scope': 'gateway_reference_only'}

    @staticmethod
    def validate_file(filename, mime, data, model, models):
        suffix = PurePosixPath(filename.replace('\\', '/')).suffix.lower()
        kind = "image" if mime in IMAGE_MIMES else "document" if suffix in DOCUMENT_MIMES and mime in (DOCUMENT_MIMES[suffix], 'application/octet-stream') else ""
        if not kind:
            raise bad("Verified uploads: PNG, JPEG, WebP, GIF images; PDF, TXT, CSV and DOCX documents. Other document formats are unconfirmed or unsupported.")
        limit = MAX_IMAGE_BYTES if kind == 'image' else MAX_FILE_BYTES
        if not data or len(data) >= limit:
            raise GatewayError(413, "attachment_size_limit", "Upload a nonempty image below 20 MiB or document below 10 MiB, matching the website's strict size check.")
        profile = models.get(model, {})
        if not profile.get("supports_images" if kind == "image" else "supports_files", False):
            raise bad("This attachment kind is not enabled for the selected model. Consult /v1/capabilities.")
        valid = (mime == "image/png" and data.startswith(b'\x89PNG\r\n\x1a\n') or
                 mime == "image/jpeg" and data.startswith(b'\xff\xd8\xff') or
                 mime == "image/webp" and data.startswith(b'RIFF') and data[8:12] == b'WEBP' or
                 mime == "image/gif" and data[:6] in (b'GIF87a', b'GIF89a') or
                 kind == 'document' and (suffix == '.pdf' and data.startswith(b'%PDF-') or
                                         suffix == '.docx' and data.startswith(b'PK\x03\x04') or
                                         suffix in ('.txt', '.csv') and b'\x00' not in data))
        if not valid:
            raise bad("File bytes do not match the declared media type.")
        name = PurePosixPath(filename.replace('\\', '/')).name
        if not name or len(name) > 180 or any(ord(c) < 32 for c in name):
            raise bad("Choose a valid filename with at most 180 characters.")
        return kind, name

    async def upload(self, client, models, model, filename, mime, data, token, image_token=None, *, chat_group_id=None, timing=None):
        kind, _ = self.validate_file(filename, mime, data, model, models)
        group = (chat_group_id or _new_conversation_id()) if kind == 'document' else ''
        key = (hashlib.sha256(token.encode()).hexdigest(), model, group, mime, hashlib.sha256(data).hexdigest())
        lock = self.upload_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.upload_locks[key] = lock
        async with lock:
            return await self._upload(client, models, model, filename, mime, data, token, image_token,
                                      chat_group_id=group, timing=timing)

    async def _upload(self, client, models, model, filename, mime, data, token, image_token=None, *, chat_group_id=None, timing=None):
        preparing = time.monotonic()
        kind, name = self.validate_file(filename, mime, data, model, models)
        self.prune()
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        digest = (fingerprint, model, hashlib.sha256(data).hexdigest())
        if timing and kind == 'image':
            timing.elapsed('image_prepare_ms', preparing, add=True)
            timing.count('image_bytes', len(data))
        if kind == "image" and digest in self.image_cache:
            if timing:
                timing.count('image_cache_hits')
            return self.get(self.image_cache[digest], model)
        if len(self.records) >= MAX_FILES:
            raise GatewayError(429, "attachment_capacity", "Too many active attachments. Release unused references or wait for expiry.")
        group = (chat_group_id or _new_conversation_id()) if kind == "document" else ""
        if kind == 'document' and chat_group_id:
            for existing in self.records.values():
                if existing.kind == 'document' and existing.group == group:
                    if existing.sha256 == digest[2] and existing.mime_type == mime and existing.credential_fingerprint == fingerprint and existing.model == model:
                        return existing
        headers = {"Origin": "https://genai.shanghaitech.edu.cn", "Referer": "https://genai.shanghaitech.edu.cn/dashboard/analysis"}
        if kind == "image":
            if not image_token:
                raise GatewayError(503, "image_credentials_required", "Import the website image credentials in the gateway page.")
            headers["token"] = image_token
            url = IMAGE_ORIGIN + "//sys/common/upload"
            fields = {"biz": "temp", "uploadType": "local"}
            params = None
        else:
            headers.update({"X-Access-Token": token, "X-Requested-With": "XMLHttpRequest"})
            url = "https://genai.shanghaitech.edu.cn/htk/chat/upload/chat/file"
            fields = {"biz": "temp"}
            params = {"chatGroupId": group, "aiType": models[model].get('upstream_model', model)}
        uploading = time.monotonic()
        try:
            if timing and kind == 'image':
                timing.count('image_upload_count')
            async with client.stream("POST", url, params=params, headers=headers, data=fields,
                                     files={"file": (name, data, mime)}, follow_redirects=False) as response:
                if response.status_code in (401, 403):
                    raise GatewayError(503, "upload_authentication_required", "The upload credential must be renewed locally.")
                if response.status_code == 413:
                    raise GatewayError(413, 'upstream_upload_size_limit', 'The website rejected the multipart upload size, including request overhead. Select a smaller attachment.')
                if response.status_code != 200:
                    raise GatewayError(502, "upload_http_error", "The upstream upload returned an unexpected HTTP status.")
                result = await read_site_json(response, max_bytes=64 * 1024 * 1024, require_success=False)
        except httpx.TimeoutException:
            raise GatewayError(504, "upload_timeout", "The upload timed out. It was not retried.") from None
        except httpx.HTTPError:
            raise GatewayError(502, "upload_connection_error", "The upload connection failed. It was not retried.") from None
        finally:
            if timing and kind == 'image':
                timing.elapsed('image_upload_ms', uploading, add=True)
        if not isinstance(result, dict) or result.get("success") is not True or type(result.get('code')) is not int or result['code'] not in (0, 200) or not isinstance(result.get("result"), dict):
            message = str(result.get('message', '')).lower() if isinstance(result, dict) else ''
            if any(word in message for word in ('token', '字符', '长度', '过长', '太长')):
                raise GatewayError(422, 'document_content_limit', 'The website rejected the document parsing or content length for this model. Use a shorter document. No local-tool fallback was performed.')
            if any(word in message for word in ('大小', '大于', '兆', 'too large', 'size limit')):
                raise GatewayError(413, 'upstream_upload_size_limit', 'The website rejected the multipart upload size, including request overhead. Select a smaller attachment.')
            raise GatewayError(502, "upload_rejected", "The upstream did not confirm a successful upload.")
        value = result["result"]
        record = Attachment("file-" + secrets.token_urlsafe(18), model, kind, "", name, len(data), int(time.time()),
                            group=group, sha256=digest[2], mime_type=mime, credential_fingerprint=fingerprint)
        if kind == "image":
            relative = value.get("url")
            if not isinstance(relative, str) or not relative.startswith('temp/') or '..' in relative.split('/') or any(c in relative for c in ['?', '#', '\\']):
                raise GatewayError(502, "upload_protocol_error", "The image upload returned an unsupported asset path.")
            record.value = STATIC_PREFIX + relative
            w, h = value.get("width"), value.get("height")
            if type(w) is not int or type(h) is not int or not 0 < w <= 100000 or not 0 < h <= 100000:
                raise GatewayError(502, "upload_protocol_error", "The image upload did not return valid dimensions.")
            record.width, record.height = w, h
        else:
            fid = value.get("fileId")
            if not isinstance(fid, str) or not fid or len(fid) > 256 or value.get("chatGroupId") != group:
                raise GatewayError(502, "upload_protocol_error", "The document upload did not return a matching file and conversation ID.")
            record.value = fid
            record.parsed_content = value.get("content") if isinstance(value.get("content"), str) else ""
            if not record.parsed_content.strip():
                raise GatewayError(422, 'document_text_unavailable', 'The website returned no extracted document text. Scanned or image-only documents are unsupported; no OCR or local-tool fallback was performed.')
        self.records[record.id] = record
        if kind == "image":
            self.image_cache[digest] = record.id
        return record

    def plan(self, request, models):
        """Validate and rewrite attachment blocks without performing uploads."""
        model = request.get('model')
        if not isinstance(model, str) or model not in models:
            raise bad("Select a configured model before attaching a file.")
        messages = request.get('messages')
        if not isinstance(messages, list):
            raise bad("Messages must be an array.")
        rewritten = dict(request)
        rewritten['messages'] = []
        plan = {}
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise bad("Each message must be an object.")
            parts = message.get('content')
            if not isinstance(parts, list):
                rewritten['messages'].append(message)
                continue
            texts, refs = [], []
            for part in parts:
                if not isinstance(part, dict):
                    raise bad("Content blocks must be objects.")
                if part.get('type') == 'text' and set(part) == {'type', 'text'} and isinstance(part['text'], str):
                    texts.append(part['text'])
                elif part.get('type') == 'file' and set(part) == {'type', 'file'}:
                    inner = part['file']
                    if not isinstance(inner, dict) or set(inner) != {'file_id'}:
                        raise bad("Use a file_id returned by this gateway's /v1/files endpoint.")
                    refs.append(self.get(inner['file_id'], model))
                elif part.get('type') == 'image_url' and set(part) == {'type', 'image_url'}:
                    image = part['image_url']
                    if not isinstance(image, dict) or set(image) - {'url', 'detail'}:
                        raise bad("Unsupported image content block.")
                    if image.get('detail', 'auto') not in ['auto', 'low', 'high']:
                        raise bad("Unsupported image detail setting.")
                    url = image.get('url')
                    if not isinstance(url, str) or not url.startswith('data:'):
                        raise bad("Use a base64 image data URL or upload an image as a gateway file. Remote URLs are not fetched.")
                    prefix, sep, encoded = url.partition(',')
                    mime = prefix.removeprefix('data:').removesuffix(';base64')
                    if not sep or prefix != 'data:' + mime + ';base64' or mime not in IMAGE_MIMES:
                        raise bad("Use a PNG, JPEG or WebP base64 image data URL.")
                    try:
                        data = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error):
                        raise bad("Invalid image base64.") from None
                    filename = 'image' + IMAGE_MIMES[mime]
                    self.validate_file(filename, mime, data, model, models)
                    refs.append((filename, mime, data))
                else:
                    raise bad("Unsupported content block. Use text, image_url data URLs, or uploaded file references.")
            if refs and message.get('role') != 'user':
                raise bad("Attachments are supported only on user messages.")
            if len(refs) > MAX_FILES:
                raise bad("The local gateway supports at most 64 attachments per message.")
            plan[index] = refs
            text = '\n'.join(texts)
            rewritten['messages'].append({**message, 'content': text if text.strip() or not refs else '[User supplied attachments.]'})
        docs = [r for refs in plan.values() for r in refs if isinstance(r, Attachment) and r.kind == 'document']
        if len({r.group for r in docs}) > 1:
            raise bad("Documents must be uploaded with the same conversation_id before being referenced together.")
        return rewritten, plan

    @staticmethod
    def mixed_text_mode(plan):
        # Retained for older internal callers: documents now always use fileIds.
        return False

    @staticmethod
    def mixed_native_mode(plan):
        refs = [r for items in plan.values() for r in items]
        return (any(isinstance(r, tuple) or r.kind == 'image' for r in refs)
                and any(isinstance(r, Attachment) and r.kind == 'document' for r in refs))

    async def materialize(self, client, models, model, plan, payload, token, image_token=None, *, bound_group=None, timing=None):
        documents = []
        last = len(payload['messages'])
        for index, refs in plan.items():
            images = []
            for ref in refs:
                # Queueing may outlive the file TTL or a user may release it.
                # Revalidate immediately before referencing it upstream.
                record = await self.upload(client, models, model, *ref, token, image_token, **({'timing': timing} if timing else {})) if isinstance(ref, tuple) else self.get(ref.id, model)
                if record.credential_fingerprint != hashlib.sha256(token.encode()).hexdigest():
                    raise GatewayError(400, 'file_credential_mismatch', 'The upstream credential changed. Re-upload the attachment before using it.')
                if record.kind == 'document':
                    documents.append(record)
                else:
                    images.append(record)
            if images:
                # Retries can leave consecutive user turns containing the
                # same immutable attachment. Upload caching alone does not
                # stop the upstream from processing its pixels twice.
                unique, positions, by_id = [], [], {}
                for record in images:
                    if record.id not in by_id:
                        unique.append(record)
                        by_id[record.id] = len(unique)
                    positions.append(by_id[record.id])
                if timing:
                    timing.count('image_input_count', len(images))
                    timing.count('image_unique_count', len(unique))
                if len(unique) < len(images):
                    note = ('\n\nImage attachment order: ' + json.dumps(positions) +
                            '. Each number refers to the corresponding unique image supplied. '
                            'Repeated numbers are identical copies of the same attachment.')
                    if index == last:
                        payload['chatInfo'] += note
                    else:
                        payload['messages'][index]['content'] += note
                images = unique
            if index == last:
                if images:
                    payload.update(imageUrl=images[0].value, imageUrls=[r.value for r in images], width=images[0].width, height=images[0].height)
            elif images:
                text = payload['messages'][index]['content']
                payload['messages'][index]['content'] = [{'type': 'text', 'text': text}] + [{'type': 'image_url', 'image_url': {'url': r.value, 'detail': 'high'}} for r in images]
        if documents:
            if bound_group is not None and any(r.group != bound_group for r in documents):
                raise GatewayError(400, 'file_conversation_mismatch', 'Upload the PDF with the same conversation_id used for chatting.')
            payload['fileIds'] = list(dict.fromkeys(r.value for r in documents))
            payload['chatGroupId'] = documents[0].group
        return payload
