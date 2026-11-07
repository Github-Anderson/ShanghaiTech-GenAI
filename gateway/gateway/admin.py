"""Authenticated management status and explicit, non-generating connection probes."""
import asyncio
import hashlib
import time

import httpx

from . import __version__
from .protocol import GatewayError
from .capabilities import PROTOCOL_VERSION


def identity(service):
    data = service.credential_store.read()
    if data is None:
        return None
    import json
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def snapshot(service, window='24h'):
    credentials = {'saved': False, 'state': 'missing', 'image_validity': 'unverified'}
    current = None
    try:
        data = service.credential_store.read()
        current = identity(service)
        if data:
            credentials.update(saved=True, state='saved', image_upload_verified_at_export=data['image']['upload_verified'])
    except GatewayError as error:
        credentials.update(state='unreadable', error_code=error.code)
    probe = service.last_probe if current == service.probe_identity else None
    return {'gateway': {'state': 'ready', 'version': __version__, 'protocol_version': PROTOCOL_VERSION,
                        'uptime_seconds': round(time.monotonic() - service.started), 'key': 'valid',
                        'pending_requests': service.pending, 'active_requests': service.running,
                        'queued_requests': max(0, service.pending - service.running - int(service.deleting)), 'max_concurrent_requests': service.settings.max_concurrent_requests},
            'statistics': service.audit.statistics(window), 'server_time': time.time(),
            'credentials': credentials, 'last_probe': probe,
            'journal': {'available': service.audit.available, 'persistent': service.audit.path is not None,
                        'request_days': 7, 'request_limit': service.audit.REQUEST_LIMIT,
                        'login_days': 365, 'login_limit': service.audit.LOGIN_LIMIT}}


async def probe_connections(service, *, automatic=False):
    current = identity(service)
    if automatic and service.last_probe and current == service.probe_identity and time.time() - service.last_probe['checked_at'] < 60:
        return service.last_probe
    if service.probe_lock.locked():
        raise GatewayError(409, 'probe_in_progress', 'A connection check is already running.')
    async with service.probe_lock:
        before = identity(service)
        async def public_page(url):
            start = time.monotonic()
            try:
                async with service.client.stream('GET', url, timeout=10, follow_redirects=False) as response:
                    return {'state': 'reachable', 'http_status': response.status_code, 'ok': 200 <= response.status_code < 400,
                            'duration_ms': round((time.monotonic() - start) * 1000)}
            except httpx.TimeoutException:
                return {'state': 'timeout'}
            except httpx.HTTPError:
                return {'state': 'unreachable'}

        async def authenticated():
            start = time.monotonic()
            try:
                if not service.settings.dynamic_site_config:
                    return {'state': 'unverified', 'reason': 'static_configuration'}
                config = await service.site_config(refresh=True)
                return {'state': 'valid', 'model_count': len(config.models), 'duration_ms': round((time.monotonic() - start) * 1000)}
            except GatewayError as error:
                return {'state': 'failed', 'error_code': error.code, 'duration_ms': round((time.monotonic() - start) * 1000)}

        async with asyncio.timeout(35):
            main, image, auth = await asyncio.gather(public_page('https://genai.shanghaitech.edu.cn/'),
                public_page('https://genaipic.shanghaitech.edu.cn/'), authenticated())
        if before != identity(service):
            raise GatewayError(409, 'credentials_changed', 'Credentials changed during the check; run it again.')
        result = {'checked_at': time.time(), 'main_page': main, 'image_service': image, 'website_authentication': auth,
                  'image_credential': 'unverified', 'model_generation': 'unverified'}
        previous = service.last_probe if service.probe_identity == before else None
        previous_auth = previous.get('website_authentication', {}) if previous else {}
        changed = (previous_auth.get('state'), previous_auth.get('error_code')) != (auth['state'], auth.get('error_code'))
        service.last_probe, service.probe_identity = result, before
        if not automatic or changed:
            service.audit.login('credential_check', auth['state'], auth.get('error_code'))
        return result
