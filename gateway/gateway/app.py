"""Local single-account gateway; upstream requests occur only on client actions."""
from __future__ import annotations

import asyncio
import hmac
import json
import time
from contextlib import asynccontextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .admin import snapshot, probe_connections
from .attachments import Attachments, MAX_IMAGE_BYTES, bad
from .audit import AuditMiddleware, record_error, request_summary
from .browser_credentials import MAX_IMPORT_BYTES, invalid
from .capabilities import describe_capabilities, PROTOCOL_VERSION
from .chat_response import chat_response
from .conversations import Conversations
from .credentials import read_gateway_key
from .protocol import GatewayError, trim_history, build_upstream_payload, error_body, validate_conversation_id
from .service import Service, delete_conversations
from .settings import load_settings
from .timings import ImageTimings, timed_output
from .tool_bridge import ToolBridge


MAX_REQUEST_BYTES = 32 * 1024 * 1024


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
            proxy=settings.upstream_proxy_url,
            limits=httpx.Limits(max_connections=2 * settings.max_concurrent_requests,
                               max_keepalive_connections=settings.max_concurrent_requests),
        )
        app.state.service = Service(settings, client)
    except GatewayError as error:
        app.state.init_error = error
    try:
        yield
    finally:
        if app.state.service is not None:
            app.state.service.audit.close()
        if client is not None:
            await client.aclose()


app = FastAPI(title="ShanghaiTech GenAI", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


app.add_middleware(AuditMiddleware)


@app.exception_handler(GatewayError)
async def gateway_error_handler(request, error):
    record_error(request, error)
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
    request.state.authorized = True
    return service


@app.get('/status', include_in_schema=False)
@app.get('/log', include_in_schema=False)
@app.get('/test', include_in_schema=False)
@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})


@app.get("/healthz")
async def health():
    return {"status": "alive", "product": "ShanghaiTech GenAI", "version": __version__, "protocol_version": PROTOCOL_VERSION, "upstream_validated": False}


CONSOLE_ASSETS = {'style.css': 'text/css', 'core.js': 'text/javascript', 'admin.js': 'text/javascript', 'test.js': 'text/javascript'}


def console_asset(name, media_type):
    async def asset():
        return FileResponse(Path(__file__).parent / 'static' / name, media_type=media_type,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    return asset


for _name, _media_type in CONSOLE_ASSETS.items():
    app.add_api_route('/' + _name, console_asset(_name, _media_type), methods=['GET'], include_in_schema=False)


@app.get('/v1/admin/status')
async def admin_status(request: Request, window: str = Query('24h', pattern='^(24h|7d)$')):
    return JSONResponse(snapshot(await authorize(request), window), headers={'Cache-Control': 'no-store'})


@app.post('/v1/admin/probe')
async def admin_probe(request: Request, automatic: bool = False):
    service = await authorize(request)
    request.state.monitoring = automatic
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
        raise GatewayError(403, 'invalid_probe_origin', 'Run the check from the gateway page.')
    try:
        result = await probe_connections(service, automatic=automatic)
    except TimeoutError:
        raise GatewayError(504, 'probe_timeout', 'The connection check timed out.') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.get('/v1/admin/logs')
async def admin_logs(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                     category: str = Query('', pattern='^(|api|management)$'),
                     outcome: str = Query('', pattern='^(|complete|failed|cancelled)$'),
                     model: str = Query('', max_length=120)):
    service = await authorize(request)
    result = service.audit.rows('request', offset, limit, category, outcome, model)
    # Do not include this log-read request in the running list.
    result['active'] = [dict(row) for row in service.audit.active.values() if row is not getattr(request.state, 'audit', None) and not outcome
                        and (not category or row.get('category') == category) and (not model or row.get('model') == model)]
    result['server_time'] = time.time()
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.get('/v1/admin/login-history')
async def login_history(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    service = await authorize(request)
    return JSONResponse(service.audit.rows('login', offset, limit), headers={'Cache-Control': 'no-store'})


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
    service.last_probe = None
    service.audit.login('credential_import', 'saved')
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
    service.last_probe = None
    service.audit.login('credential_logout', 'complete')
    service.attachments = Attachments()
    service.conversations = Conversations(service.conversations.path)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.get("/readyz")
async def ready(request: Request):
    service = await authorize(request)
    await asyncio.to_thread(service.credentials.get_token)
    return {"status": "locally_configured", "upstream_validated": False, "protocol_version": PROTOCOL_VERSION}


@app.get("/v1/models")
async def models(request: Request, refresh: bool = False):
    service = await authorize(request)
    config = await service.site_config(refresh=refresh)
    body = {"object": "list", "data": [{"id": name, "name": profile.get('display_name', name), "object": "model", "created": 0, "owned_by": "ShanghaiTech"} for name, profile in config.models.items()]}
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@app.delete('/v1/conversations/{conversation_id}')
async def delete_conversation(conversation_id: str, request: Request):
    service = await authorize(request)
    result = await delete_conversations(service, [conversation_id])
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.post('/v1/conversations/delete')
async def delete_conversation_batch(request: Request):
    service = await authorize(request)
    raw = await read_limited(request, 65536)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'conversation_ids'}:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise GatewayError(400, 'invalid_delete_request', 'Supply a JSON object with conversation_ids only.') from None
    result = await delete_conversations(service, data['conversation_ids'])
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.get('/v1/capabilities')
async def capabilities(request: Request, refresh: bool = False):
    service = await authorize(request)
    config = await service.site_config(refresh=refresh)
    return JSONResponse(describe_capabilities(config.models, config.message_round), headers={'Cache-Control': 'no-store'})


async def read_limited(request, limit, message='The request exceeds the local upload size limit.'):
    result = bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result) > limit:
            raise GatewayError(413, 'request_too_large', message)
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
    config = await service.site_config()
    model = fields.get('model')
    if not isinstance(model, str) or model not in config.models:
        raise bad('Supply the selected gateway model in the model field.')
    conversation_id = validate_conversation_id(fields.get('conversation_id'))
    if fields.get('include_content', 'true') not in ('true', 'false'):
        raise bad('include_content must be true or false.')
    kind, _ = service.attachments.validate_file(*uploaded, model, config.models)
    async with service.slot():
        token = await asyncio.to_thread(service.credentials.get_token)
        config.check_token(token)
        image_token = await service.image_token() if kind == 'image' else None
        group = service.conversations.group_for(token, model, conversation_id) if conversation_id is not None else None
        async with service.conversations.lock_group(group or '__unbound_upload__'):
            record = await service.attachments.upload(service.client, config.models, model, *uploaded, token, image_token, chat_group_id=group, image_base=config.image_base)
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
    chunks = await read_limited(request, MAX_REQUEST_BYTES, "The request exceeds the demo size limit.")
    try:
        data = json.loads(chunks)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise GatewayError(400, "invalid_json", "Send a JSON object.") from None


@app.post("/v1/chat/completions")
async def completions(request: Request):
    started = time.monotonic()
    service = await authorize(request)
    data = await read_request(request)
    bridge = ToolBridge(data)
    config = await service.site_config()
    if hasattr(request.state, 'audit'):
        request.state.audit.update(request_summary(data, config.models))
    adapted = trim_history(bridge.rewrite(data), config.message_round)
    rewritten, attachment_plan = service.attachments.plan(adapted, config.models)
    payload, model = build_upstream_payload(rewritten, config.models, config.message_round)
    has_images = any(isinstance(ref, tuple) or ref.kind == 'image' for refs in attachment_plan.values() for ref in refs)
    timing = ImageTimings(started) if has_images else None
    if timing:
        timing.elapsed('request_prepare_ms', started)
        timing.count('upstream_request_bytes', len(json.dumps(payload, ensure_ascii=False).encode('utf8')))
        timing.count('tool_count', len(bridge.tools))
    root = config.models[model]["root_ai_type"]
    options = {'conversation_id': data['conversation_id']} if data.get('conversation_id') is not None else {}
    source_events = service.events(payload, root, attachment_plan, model, bridge=bridge, config=config, **options, **({'timing': timing} if timing else {}))
    events = timed_output(source_events, timing)
    return await chat_response(request, data, events, model)
