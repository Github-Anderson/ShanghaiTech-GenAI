"""Local single-account gateway; upstream requests occur only on client actions."""
from __future__ import annotations

import asyncio
import copy
import hmac
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from email.parser import BytesParser
from email import policy

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .credentials import read_gateway_key
from .browser_credentials import BrowserCredentialStore, BrowserCredentials, MAX_IMPORT_BYTES, invalid
from .attachments import Attachments, MAX_FILE_BYTES, MAX_IMAGE_BYTES, bad
from .protocol import GatewayError, build_upstream_payload, error_body, make_chunk, make_completion
from .settings import load_settings
from .upstream import stream_upstream
from .site_api import fetch_sources
from .tool_bridge import ToolBridge
from .capabilities import describe_capabilities, PROTOCOL_VERSION
from .conversations import Conversations, validate_conversation_id
from .timings import ImageTimings, timed_output
from . import __version__

MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_CHARS = 2_000_000
logger = logging.getLogger(__name__)


def payload_has_images(payload):
    """Inspect the materialized wire payload, including historical images."""
    if payload.get('imageUrl') or payload.get('imageUrls'):
        return True
    return any(isinstance(message.get('content'), list)
               and any(isinstance(part, dict) and part.get('type') == 'image_url'
                       for part in message['content'])
               for message in payload.get('messages', []) if isinstance(message, dict))


class Service:
    def __init__(self, settings, client):
        self.settings = settings
        self.client = client
        self.credential_store = BrowserCredentialStore(getattr(settings, 'imported_credentials_file', None))
        self.credentials = BrowserCredentials(self.credential_store, getattr(settings, 'site_signing_file', None))
        self.image_credentials = BrowserCredentials(self.credential_store, image=True)
        self.attachments = Attachments()
        self.conversations = Conversations()
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self.pending = 0

    @asynccontextmanager
    async def slot(self):
        if self.pending >= self.settings.max_waiting_requests + self.settings.max_concurrent_requests:
            raise GatewayError(429, "queue_full", "The local account queue is full.")
        self.pending += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(self.semaphore.acquire(), timeout=self.settings.queue_timeout_seconds)
                acquired = True
            except TimeoutError:
                raise GatewayError(429, "queue_timeout", "The local account queue timed out.") from None
            yield
        finally:
            if acquired:
                self.semaphore.release()
            self.pending -= 1

    async def events(self, payload, root_ai_type, attachment_plan=None, public_model=None, conversation_id=None, timing=None):
        waiting = time.monotonic()
        async with self.slot():
            if timing:
                timing.elapsed('queue_wait_ms', waiting)
            preparing = time.monotonic()
            token = await asyncio.to_thread(self.credentials.get_token)
            signing = await asyncio.to_thread(self.credentials.get_signing, token) if payload.get('netGo') else None
            bound_group = None
            if conversation_id is not None:
                bound_group = self.conversations.group_for(token, public_model or payload['aiType'], conversation_id)
                payload = {**payload, 'chatGroupId': bound_group}
            if attachment_plan:
                needs_upload = any(isinstance(ref, tuple) for refs in attachment_plan.values() for ref in refs)
                image_token = await self.image_token() if needs_upload else None
                if timing:
                    timing.elapsed('credentials_ms', preparing)
                preparing = time.monotonic()
                payload = await self.attachments.materialize(self.client, self.settings.models, public_model or payload['aiType'], attachment_plan, payload, token, image_token, bound_group=bound_group, **({'timing': timing} if timing else {}))
                if timing:
                    timing.elapsed('attachment_materialize_ms', preparing)
            waiting = time.monotonic()
            async with self.conversations.lock_group(payload['chatGroupId']):
                if timing:
                    timing.elapsed('conversation_wait_ms', waiting)
                if attachment_plan and self.attachments.mixed_native_mode(attachment_plan):
                    yield {'attachment_mode': 'website_file_ids_and_images'}
                generated = self.generate(payload, root_ai_type, token, signing, **({'timing': timing} if timing else {}))
                try:
                    async for event in generated:
                        yield event
                finally:
                    with anyio.CancelScope(shield=True):
                        await generated.aclose()

    async def generation_stream(self, payload, root_ai_type, token, timing=None):
        """Recover one empty or pre-output image-server failure for DeepSeek.

        Runs inside the existing account slot, conversation lock and total
        deadline. Attachments are already materialized and are never uploaded
        again. Only a classified site 500 may retry an image generation;
        partial output, timeout, auth, quota and input failures are not replayed.
        """
        for attempt in range(2):
            visible_output = False
            retry = False
            upstream = stream_upstream(self.client, payload, root_ai_type, token,
                                       idle_timeout=self.settings.read_timeout_seconds,
                                       **({'header_timeout': max(self.settings.read_timeout_seconds,
                                                                 self.settings.total_timeout_seconds)}
                                          if payload_has_images(payload) else {}),
                                       **({'timing': timing} if timing else {}))
            if timing:
                timing.count('upstream_attempts')
            try:
                async for event in upstream:
                    visible_output |= bool(event.get('content') or event.get('reasoning_content')
                                           or event.get('tool_calls') or event.get('finish_reason'))
                    yield event
                return
            except GatewayError as error:
                empty = error.code == 'upstream_empty_stream'
                image_server_error = (error.code == 'upstream_error' and error.retryable
                                      and payload_has_images(payload))
                if (attempt != 0 or payload.get('aiType') != 'deepseek-pro'
                        or not (empty or image_server_error) or visible_output):
                    raise
                retry = 'empty' if empty else 'image_server_500'
            finally:
                with anyio.CancelScope(shield=True):
                    await upstream.aclose()
            if retry:
                if retry == 'empty':
                    logger.warning('upstream_empty_recovery model=deepseek-pro retry=1')
                else:
                    logger.warning('upstream_image_recovery model=deepseek-pro reason=server_500 retry=1')
                await asyncio.sleep(0.25)

    async def generate(self, payload, root_ai_type, token, signing, timing=None):
        source_snapshot = copy.deepcopy(payload)
        upstream = self.generation_stream(payload, root_ai_type, token, **({'timing': timing} if timing else {}))
        deadline = time.monotonic() + self.settings.total_timeout_seconds
        size = 0
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GatewayError(504, "upstream_timeout", "The upstream total time limit was reached.")
                try:
                    event = await asyncio.wait_for(anext(upstream), timeout=remaining)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise GatewayError(504, "upstream_timeout", "The upstream total time limit was reached.") from None
                size += len(event.get("content", "")) + len(event.get("reasoning_content", ""))
                if size > MAX_OUTPUT_CHARS:
                    raise GatewayError(502, "upstream_output_limit", "The ShanghaiTech GenAI output size limit was reached.")
                if event.get("finish_reason"):
                    if signing is not None:
                        try:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError()
                            sources = await asyncio.wait_for(fetch_sources(
                                self.client, source_snapshot, token, signing), remaining)
                            yield {'sources': sources, 'sources_status': 'ready'}
                        except GatewayError as error:
                            yield {'sources_status': 'failed', 'sources_error': error.code}
                        except TimeoutError:
                            yield {'sources_status': 'failed', 'sources_error': 'sources_timeout'}
                    yield event
                    return
                yield event
        finally:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()

    async def image_token(self):
        if self.image_credentials is None:
            raise GatewayError(503, 'upstream_login_required', 'Import browser credential JSON to log in to the gateway.')
        return await asyncio.to_thread(self.image_credentials.get_token)


@asynccontextmanager
async def lifespan(app):
    app.state.service = None
    app.state.init_error = None
    client = None
    try:
        settings = load_settings()
        await asyncio.to_thread(read_gateway_key, settings.gateway_key_file)
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=settings.connect_timeout_seconds, read=settings.read_timeout_seconds, write=30, pool=10),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=2 * settings.max_concurrent_requests,
                               max_keepalive_connections=settings.max_concurrent_requests),
        )
        app.state.service = Service(settings, client)
    except GatewayError as error:
        app.state.init_error = error
    try:
        yield
    finally:
        if client is not None:
            await client.aclose()


app = FastAPI(title="ShanghaiTech GenAI", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(GatewayError)
async def gateway_error_handler(request, error):
    return JSONResponse(error_body(error), status_code=error.status_code, headers={"Cache-Control": "no-store"})


@app.exception_handler(Exception)
async def unexpected_error_handler(request, error):
    safe = GatewayError(500, "internal_error", "ShanghaiTech GenAI encountered an internal error. No upstream details were logged.")
    return JSONResponse(error_body(safe), status_code=500, headers={"Cache-Control": "no-store"})


def get_service(request):
    service = request.app.state.service
    if service is None:
        raise request.app.state.init_error or GatewayError(503, "configuration_required", "Configure ShanghaiTech GenAI and restart it.")
    return service


async def authorize(request):
    service = get_service(request)
    key = await asyncio.to_thread(read_gateway_key, service.settings.gateway_key_file)
    value = request.headers.get("authorization", "")
    scheme, _, provided = value.partition(" ")
    if scheme.lower() != "bearer" or not provided.isascii() or not hmac.compare_digest(provided, key):
        raise GatewayError(401, "invalid_api_key", "A valid local gateway API key is required.")
    return service


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})


@app.get("/healthz")
async def health():
    return {"status": "alive", "product": "ShanghaiTech GenAI", "version": __version__, "protocol_version": PROTOCOL_VERSION, "upstream_validated": False}


@app.get('/credential-import.js', include_in_schema=False)
async def credential_import_script():
    return FileResponse(Path(__file__).parent / 'static' / 'credential-import.js',
                        media_type='text/javascript', headers={'Cache-Control': 'no-store'})


@app.post('/v1/credentials/import')
async def import_browser_credentials(request: Request):
    service = await authorize(request)
    origin = request.headers.get('origin')
    if origin is not None and origin != f'{request.url.scheme}://{request.url.netloc}':
        raise GatewayError(403, 'invalid_import_origin', 'Import credentials from the gateway page itself.')
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise invalid()
    if service.credential_store.path is None:
        raise GatewayError(503, 'credentials_import_disabled', 'Configure imported_credentials_file and restart the gateway.')
    body = await read_limited(request, MAX_IMPORT_BYTES)
    try:
        data = json.loads(body.decode('utf-8-sig'))
    except (UnicodeError, ValueError, RecursionError):
        raise invalid() from None
    if service.pending:
        raise GatewayError(409, 'credentials_in_use', 'Wait for active or queued chat requests to finish before importing.')
    # A small synchronous atomic write keeps the credential switch indivisible
    # with respect to other handlers in this single-worker gateway.
    result = service.credential_store.save(data)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.get('/v1/credentials/status')
async def browser_credentials_status(request: Request):
    service = await authorize(request)
    data = await asyncio.to_thread(service.credential_store.read)
    result = {'logged_in': data is not None, 'upstream_validated': False}
    if data is not None:
        result['image_upload_verified_at_export'] = data['image']['upload_verified']
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.post('/v1/credentials/logout')
async def logout_browser_credentials(request: Request):
    service = await authorize(request)
    origin = request.headers.get('origin')
    if origin is not None and origin != f'{request.url.scheme}://{request.url.netloc}':
        raise GatewayError(403, 'invalid_import_origin', 'Manage credentials from the gateway page itself.')
    if service.pending:
        raise GatewayError(409, 'credentials_in_use', 'Wait for active or queued chat requests to finish before logging out.')
    result = service.credential_store.logout()
    service.attachments = Attachments()
    service.conversations = Conversations()
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.get("/readyz")
async def ready(request: Request):
    service = await authorize(request)
    await asyncio.to_thread(service.credentials.get_token)
    return {"status": "locally_configured", "upstream_validated": False, "protocol_version": PROTOCOL_VERSION}


@app.get("/v1/models")
async def models(request: Request):
    service = await authorize(request)
    body = {"object": "list", "data": [{"id": name, "name": profile.get('display_name', name), "object": "model", "created": 0, "owned_by": "ShanghaiTech"} for name, profile in service.settings.models.items()]}
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@app.get('/v1/capabilities')
async def capabilities(request: Request):
    service = await authorize(request)
    return JSONResponse(describe_capabilities(service.settings.models), headers={'Cache-Control': 'no-store'})


async def read_limited(request, limit):
    result = bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result) > limit:
            raise GatewayError(413, 'request_too_large', 'The request exceeds the local upload size limit.')
    return bytes(result)


@app.post('/v1/files')
async def upload_file(request: Request):
    service = await authorize(request)
    mime = request.headers.get('content-type', '')
    if not mime.lower().startswith('multipart/form-data;') or len(mime) > 1024 or '\r' in mime or '\n' in mime:
        raise bad('Send multipart/form-data with file, model and optional purpose=user_data fields.')
    body = await read_limited(request, MAX_IMAGE_BYTES + 65536)
    message = BytesParser(policy=policy.default).parsebytes(('Content-Type: ' + mime + '\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii') + body)
    if not message.is_multipart() or message.defects:
        raise bad('Malformed multipart upload.')
    fields, uploaded = {}, None
    parts = list(message.iter_parts())
    if len(parts) > 5:
        raise bad('Upload one file at a time.')
    for part in parts:
        name = part.get_param('name', header='content-disposition')
        if part.is_multipart() or name not in ['file', 'model', 'purpose', 'conversation_id', 'include_content'] or name in fields:
            raise bad('Unsupported or repeated multipart field.')
        value = part.get_payload(decode=True)
        if not isinstance(value, bytes):
            raise bad('Malformed multipart field.')
        fields[name] = True
        if name == 'file':
            uploaded = (part.get_filename() or 'upload', part.get_content_type(), value)
        else:
            try:
                if len(value) > 256:
                    raise ValueError()
                fields[name] = value.decode('utf8')
            except (UnicodeError, ValueError):
                raise bad('Invalid multipart metadata.') from None
    if not uploaded or fields.get('purpose', 'user_data') != 'user_data':
        raise bad('Supply one file and purpose=user_data.')
    model = fields.get('model')
    if not isinstance(model, str) or model not in service.settings.models:
        raise bad('Supply the selected gateway model in the model field.')
    conversation_id = validate_conversation_id(fields.get('conversation_id'))
    if fields.get('include_content', 'true') not in ('true', 'false'):
        raise bad('include_content must be true or false.')
    kind, _ = service.attachments.validate_file(*uploaded, model, service.settings.models)
    async with service.slot():
        token = await asyncio.to_thread(service.credentials.get_token)
        image_token = await service.image_token() if kind == 'image' else None
        group = service.conversations.group_for(token, model, conversation_id) if conversation_id is not None else None
        async with service.conversations.lock_group(group or '__unbound_upload__'):
            record = await service.attachments.upload(service.client, service.settings.models, model, *uploaded, token, image_token, chat_group_id=group)
    return JSONResponse(record.public(include_content=fields.get('include_content') != 'false'), headers={'Cache-Control': 'no-store'})


@app.get('/v1/files/{file_id}')
async def file_info(file_id: str, request: Request):
    service = await authorize(request)
    record = service.attachments.get(file_id)
    return JSONResponse(record.public(), headers={'Cache-Control': 'no-store'})


@app.delete('/v1/files/{file_id}')
async def delete_file(file_id: str, request: Request):
    service = await authorize(request)
    return JSONResponse(service.attachments.delete(file_id), headers={'Cache-Control': 'no-store'})


async def read_request(request):
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_REQUEST_BYTES:
            raise GatewayError(413, "request_too_large", "The request exceeds the demo size limit.")
    try:
        data = json.loads(chunks)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise GatewayError(400, "invalid_json", "Send a JSON object.") from None


def sse(data):
    return "data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"


def collect_metadata(target, event):
    if 'usage' in event:
        target['usage'] = event['usage']
    for key in ('sources', 'sources_status', 'sources_error', 'upstream_metadata', 'tool_routing', 'attachment_mode', 'timings'):
        if key in event:
            target.setdefault('shanghaitech', {})[key] = event[key]


async def close_events(events):
    with anyio.CancelScope(shield=True):
        await events.aclose()


async def watch_disconnect(request, owner, stopped):
    """Aggregation has no StreamingResponse disconnect listener of its own."""
    while not stopped.is_set():
        if await request.is_disconnected():
            if not stopped.is_set():
                owner.cancel()
            return
        await asyncio.sleep(0.25)


@app.post("/v1/chat/completions")
async def completions(request: Request):
    started = time.monotonic()
    service = await authorize(request)
    data = await read_request(request)
    bridge = ToolBridge(data)
    adapted = bridge.rewrite(data)
    rewritten, attachment_plan = service.attachments.plan(adapted, service.settings.models)
    payload, model = build_upstream_payload(rewritten, service.settings.models)
    has_images = any(isinstance(ref, tuple) or ref.kind == 'image' for refs in attachment_plan.values() for ref in refs)
    timing = ImageTimings(started) if has_images else None
    if timing:
        timing.elapsed('request_prepare_ms', started)
        timing.count('upstream_request_bytes', len(json.dumps(payload, ensure_ascii=False).encode('utf8')))
        timing.count('tool_count', len(bridge.tools))
    root = service.settings.models[model]["root_ai_type"]
    options = {'conversation_id': data['conversation_id']} if data.get('conversation_id') is not None else {}
    source_events = service.events(payload, root, attachment_plan, model, **options, **({'timing': timing} if timing else {}))
    events = timed_output(bridge.convert(source_events), timing)
    response_id = "chatcmpl-" + secrets.token_hex(12)
    created = int(time.time())
    if not data.get("stream", False):
        text, reasoning, calls, finish = [], [], [], None
        metadata = {}
        stopped = asyncio.Event()
        watcher = asyncio.create_task(watch_disconnect(request, asyncio.current_task(), stopped))
        try:
            async for event in events:
                collect_metadata(metadata, event)
                if "content" in event:
                    text.append(event["content"])
                if "reasoning_content" in event:
                    reasoning.append(event["reasoning_content"])
                if 'tool_calls' in event:
                    calls.extend(event['tool_calls'])
                if event.get("finish_reason"):
                    finish = event["finish_reason"]
            if finish is None:
                raise GatewayError(502, "upstream_incomplete", "The upstream stream ended without a recognized completion signal.")
            result = make_completion(response_id, created, model, "".join(text), finish_reason=finish)
            result.update(metadata)
            if reasoning:
                result["choices"][0]["message"]["reasoning_content"] = "".join(reasoning)
            if calls:
                result['choices'][0]['message'].update(content=None, tool_calls=[{k:v for k,v in call.items() if k != 'index'} for call in calls])
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        finally:
            # Request.is_disconnected uses an AnyIO cancel scope. If a cancel
            # races that scope it may be consumed; the flag still ends the loop.
            stopped.set()
            watcher.cancel()
            with anyio.CancelScope(shield=True):
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass
            await close_events(events)

    # Before StreamingResponse starts, it cannot observe client disconnects.
    # Cancel prefetch too, otherwise a cancelled DSH turn holds an account slot
    # until the upstream finishes (up to the total request timeout).
    stopped = asyncio.Event()
    watcher = asyncio.create_task(watch_disconnect(request, asyncio.current_task(), stopped))
    try:
        first = await anext(events)
    except StopAsyncIteration:
        await close_events(events)
        raise GatewayError(502, "upstream_incomplete", "The upstream returned no recognized events.") from None
    except BaseException:
        await close_events(events)
        raise
    finally:
        stopped.set()
        watcher.cancel()
        with anyio.CancelScope(shield=True):
            try:
                await watcher
            except asyncio.CancelledError:
                pass

    async def output():
        try:
            yield sse(make_chunk(response_id, created, model, {"role": "assistant"}))
            event = first
            metadata = {}
            while True:
                collect_metadata(metadata, event)
                delta = {key: event[key] for key in ["content", "reasoning_content", "tool_calls"] if key in event}
                if delta:
                    yield sse(make_chunk(response_id, created, model, delta))
                if event.get("finish_reason"):
                    final = make_chunk(response_id, created, model, {}, finish_reason=event["finish_reason"])
                    if 'shanghaitech' in metadata:
                        final['shanghaitech'] = metadata['shanghaitech']
                    yield sse(final)
                    if data.get('stream_options', {}).get('include_usage') and 'usage' in metadata:
                        usage = make_chunk(response_id, created, model, {})
                        usage.update(choices=[], usage=metadata['usage'])
                        yield sse(usage)
                    yield "data: [DONE]\n\n"
                    return
                try:
                    event = await anext(events)
                except StopAsyncIteration:
                    raise GatewayError(502, "upstream_incomplete", "The upstream stream ended without a recognized completion signal.") from None
        except GatewayError as error:
            yield sse(error_body(error))
        except asyncio.CancelledError:
            raise
        except Exception:
            yield sse(error_body(GatewayError(502, "upstream_stream_error", "The upstream stream could not be completed.")))
        finally:
            await close_events(events)

    return StreamingResponse(output(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"}, background=BackgroundTask(close_events, events))
