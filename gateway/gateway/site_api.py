"""Signed website requests using explicitly configured signing material."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from .protocol import GatewayError

ORIGIN = 'https://genai.shanghaitech.edu.cn'
SEARCH_PATH = '/htk/chat/net/search'
MAX_JSON_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class SigningCredentials:
    salt: str = field(repr=False)
    tenant_id: str = field(repr=False)


def signed_params(path: str, params: dict, salt: str) -> tuple[dict, str]:
    """Match getSign: shallow sort, top-level numbers to strings, then MD5.

    Call before appending the interceptor's GET _t. Empty values participate.
    Chat fields use integers; rejecting floats avoids Python/JS number-format
    differences rather than silently signing a different JSON representation.
    """
    merged = dict(parse_qsl(urlsplit(path).query, keep_blank_values=True))
    segment = urlsplit(path).path.rsplit('/', 1)[-1]
    if ',' in segment:
        merged['x-path-variable'] = unquote(segment)
    merged.update(copy.deepcopy(params))
    for key, value in merged.items():
        if type(value) is int:
            if abs(value) > 2**53 - 1:
                raise GatewayError(400, 'invalid_signing_parameters', 'Signing integers must be exactly representable in JavaScript.')
            merged[key] = str(value)
        elif type(value) is float:
            raise GatewayError(400, 'invalid_signing_parameters', 'Use integer values for numeric site parameters.')
    normalized = dict(sorted(merged.items(), key=lambda item: item[0].encode('utf-16-be')))
    try:
        encoded = (json.dumps(normalized, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + salt).encode('utf8')
    except (TypeError, ValueError, UnicodeError):
        raise GatewayError(400, 'invalid_signing_parameters', 'Site signing parameters must be valid JSON.') from None
    return normalized, hashlib.md5(encoded).hexdigest().upper()


def signed_headers(sign: str, token: str, credentials: SigningCredentials) -> dict:
    values = (token, credentials.tenant_id, credentials.salt)
    if any(not isinstance(v, str) or not v or '\r' in v or '\n' in v for v in values):
        raise GatewayError(503, 'upstream_signing_required', 'Configure authorized site signing credentials locally.')
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d%H%M%S')
    return {'X-Access-Token': token, 'X-Sign': sign, 'X-TIMESTAMP': stamp,
            'tenant-id': credentials.tenant_id, 'Accept': 'application/json'}


async def read_site_json(response: httpx.Response, max_bytes=MAX_JSON_BYTES, require_success=True) -> dict:
    if response.status_code in (401, 403):
        raise GatewayError(503, 'upstream_authentication_required', 'The upstream credential must be renewed locally.')
    if response.status_code != 200:
        raise GatewayError(502, 'site_http_error', 'The site returned an unexpected HTTP status.')
    if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise GatewayError(502, 'site_content_type_error', 'The site did not return JSON.')
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > max_bytes:
            raise GatewayError(502, 'site_response_limit', 'The site JSON response exceeds the local size limit.')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise GatewayError(502, 'site_protocol_error', 'The site returned malformed JSON.') from None
    if not isinstance(value, dict) or require_success and (value.get('success') is not True or type(value.get('code')) is not int or value['code'] not in (0, 200)):
        raise GatewayError(502, 'site_business_error', 'The site did not confirm a successful operation.')
    return value


async def signed_get(client, path, params, token, credentials):
    """Allow only same-origin /htk GETs; _t is appended after signing."""
    if not path.startswith('/htk/') or urlsplit(path).netloc or urlsplit(path).fragment:
        raise GatewayError(400, 'invalid_site_path', 'Select an allowed site API path.')
    normalized, sign = signed_params(path, params, credentials.salt)
    headers = signed_headers(sign, token, credentials)
    if '/sys/dict/getDictItems' not in path:
        normalized = {'_t': int(datetime.now(timezone.utc).timestamp()), **normalized}
    try:
        async with client.stream('GET', ORIGIN + urlsplit(path).path, params=normalized,
                                 headers=headers, follow_redirects=False) as response:
            return await read_site_json(response)
    except httpx.TimeoutException:
        raise GatewayError(504, 'site_timeout', 'The site request timed out.') from None
    except httpx.HTTPError:
        raise GatewayError(502, 'site_connection_error', 'The site request could not be completed.') from None


async def fetch_sources(client, payload, token, credentials):
    """Post the completed turn's snapshot, never a reconstructed next turn."""
    normalized, sign = signed_params(SEARCH_PATH, payload, credentials.salt)
    headers = signed_headers(sign, token, credentials)
    headers['Content-Type'] = 'application/json'
    try:
        async with client.stream('POST', ORIGIN + SEARCH_PATH, json=normalized,
                                 headers=headers, follow_redirects=False) as response:
            envelope = await read_site_json(response)
    except httpx.TimeoutException:
        raise GatewayError(504, 'sources_timeout', 'The source lookup timed out. The answer remains complete.') from None
    except httpx.HTTPError:
        raise GatewayError(502, 'sources_connection_error', 'The source lookup failed. The answer remains complete.') from None
    result = envelope.get('result')
    if (not isinstance(result, dict) or not isinstance(result.get('links'), list)
            or type(result.get('count')) is not int or result['count'] < 0):
        raise GatewayError(502, 'sources_protocol_error', 'The site returned an unsupported source list.')
    links = []
    for link in result['links']:
        if (not isinstance(link, dict) or type(link.get('num')) is not int
                or any(not isinstance(link.get(k), str) for k in ('title', 'link', 'snippet'))):
            raise GatewayError(502, 'sources_protocol_error', 'The site returned an unsupported source entry.')
        try:
            address = urlsplit(link['link'])
        except ValueError:
            raise GatewayError(502, 'sources_protocol_error', 'The site returned an unsupported source URL.') from None
        if address.scheme not in ('http', 'https') or not address.hostname or address.username or address.password:
            raise GatewayError(502, 'sources_protocol_error', 'The site returned an unsupported source URL.')
        links.append({k: link[k] for k in ('num', 'title', 'link', 'snippet')})
    return {'links': links, 'count': result['count']}
