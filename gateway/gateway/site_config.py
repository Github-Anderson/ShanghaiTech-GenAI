"""Atomic, credential-scoped snapshots of the website configuration."""
from __future__ import annotations
import asyncio
import hashlib
import time
from dataclasses import dataclass, replace, field
from urllib.parse import urlsplit

from .protocol import GatewayError
from .site_api import signed_get

DEFAULT_IMAGE_BASE = 'https://genaipic.shanghaitech.edu.cn/'

@dataclass(frozen=True)
class SiteConfig:
    models: dict
    message_round: int = 30
    image_base: str = DEFAULT_IMAGE_BASE
    credential_fingerprint: str = field(default="", repr=False)

    def check_token(self, token):
        if self.credential_fingerprint and self.credential_fingerprint != hashlib.sha256(token.encode()).hexdigest():
            raise GatewayError(409, "site_configuration_changed", "Website credentials changed. Retry with the current model catalog.")

def parse_config(catalog, config, dictionary, local_models=None):
    try:
        limit = config['result']['maxTokens']
        rounds = config['result']['messageRound']
        if type(limit) is not int or not 0 < limit <= 10000000 or type(rounds) is not int or not 0 <= rounds <= 10000:
            raise ValueError()
        base = dictionary['result'][0]['value']
        url = urlsplit(base)
        if (url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.shanghaitech.edu.cn')
                or url.username or url.password or url.query or url.fragment or url.port not in (None,443)):
            raise ValueError()
        base = base.rstrip('/') + '/'
        models = {}
        for record in catalog['result']['records']:
            if str(record.get('status')) not in ('1', '3'):
                continue
            model, root = record['aiType'], record['rootAiType']
            if not isinstance(model, str) or not model or len(model)>128 or root not in ('azure','xinference'):
                raise ValueError()
            if (local_models or {}).get(model, {}).get('enabled') is False:
                continue
            thinking = record.get('enableDeepThink') in (1, '1')
            models[model] = {'root_ai_type': root, 'upstream_model': model,
                'display_name': record.get('aiName') or model, 'max_tokens': limit,
                'enable_thinking': thinking, 'supports_thinking': thinking, 'thinking': False,
                'supports_search': True, 'net_go': False, 'supports_files': True,
                'supports_images': str(record.get('status')) == '3'}
        if not models:
            raise ValueError()
        return SiteConfig(models, rounds, base)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        raise GatewayError(502, 'site_configuration_invalid', 'The website returned incomplete model or chat configuration.') from None

class SiteCatalog:
    def __init__(self, client, credentials, local_models=None, ttl=300):
        self.client, self.credentials, self.local_models = client, credentials, local_models
        self.ttl, self.lock = ttl, asyncio.Lock()
        self.value, self.identity, self.expires = None, None, 0

    async def get(self, *, refresh=False):
        token = await asyncio.to_thread(self.credentials.get_token)
        signing = await asyncio.to_thread(self.credentials.get_signing, token)
        identity = hashlib.sha256((token + '\0' + signing.tenant_id + '\0' + signing.salt).encode()).digest()
        async with self.lock:
            if not refresh and self.value is not None and identity == self.identity and time.monotonic() < self.expires:
                return self.value
            # Commit all three responses together; never mix old and new settings.
            catalog, config, dictionary = await asyncio.gather(
                signed_get(self.client, '/htk/ai/aiModel/list', {'pageNo':1,'pageSize':999,'showStatusList':'2,3'}, token, signing),
                signed_get(self.client, '/htk/base-config/detail', {}, token, signing),
                signed_get(self.client, '/htk/sys/dict/getDictItems/file_url', {}, token, signing))
            value = replace(parse_config(catalog, config, dictionary, self.local_models),
                            credential_fingerprint=hashlib.sha256(token.encode()).hexdigest())
            self.value, self.identity, self.expires = value, identity, time.monotonic() + self.ttl
            return value
