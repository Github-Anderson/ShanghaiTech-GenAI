"""Validated, atomic browser credential imports; no secret values in responses."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .protocol import GatewayError
from .site_api import SigningCredentials

MAX_IMPORT_BYTES = 65536
SCHEMA = 'shanghaitech-browser-credentials/v1'


def invalid():
    return GatewayError(400, 'invalid_credentials_import',
                        'Select a complete ShanghaiTech GenAI browser credential export JSON. No values are returned.')


def validate_import(data):
    def value(section, name, minimum):
        text = data[section]['headers'][name]
        if (not isinstance(text, str) or not minimum <= len(text) <= 16384
                or not all(33 <= ord(c) <= 126 for c in text)
                or any(marker in text.lower() for marker in ('redacted', 'placeholder', '<', '>'))):
            raise ValueError()
        return text

    try:
        if (data['schema'] != SCHEMA or data['main']['origin'] != 'https://genai.shanghaitech.edu.cn'
                or data['image']['origin'] != 'https://genaipic.shanghaitech.edu.cn'
                or type(data['image']['upload_verified']) is not bool):
            raise ValueError()
        # Persist only defined fields, never arbitrary file content or URLs.
        result = {
            'schema': SCHEMA,
            'main': {'origin': data['main']['origin'], 'headers': {
                'X-Access-Token': value('main', 'X-Access-Token', 8),
                'tenant-id': value('main', 'tenant-id', 1)}},
            'image': {'origin': data['image']['origin'], 'headers': {
                'token': value('image', 'token', 8)}, 'upload_verified': data['image']['upload_verified']},
        }
        if 'signing' in data:
            salt = data['signing']['salt']
            if (not isinstance(salt, str) or not 1 <= len(salt) <= 256
                    or not all(33 <= ord(c) <= 126 for c in salt)):
                raise ValueError()
            result['signing'] = {'salt': salt}
        return result
    except (KeyError, TypeError, ValueError):
        raise invalid() from None


class BrowserCredentialStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if self.path is None:
                return None
            try:
                with self.path.open('rb') as source:
                    body = source.read(MAX_IMPORT_BYTES + 1)
                if len(body) > MAX_IMPORT_BYTES:
                    raise ValueError()
                return validate_import(json.loads(body.decode('utf-8-sig')))
            except FileNotFoundError:
                return None
            except (OSError, UnicodeError, ValueError, GatewayError, RecursionError):
                raise GatewayError(503, 'imported_credentials_unreadable',
                                   'Saved browser credentials cannot be read. Import a valid export again.') from None

    def save(self, data):
        data = validate_import(data)
        if self.path is None:
            raise GatewayError(503, 'credentials_import_disabled', 'Configure imported_credentials_file and restart the gateway.')
        temporary = None
        with self.lock:
            try:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix='.credentials-', dir=self.path.parent)
                with os.fdopen(fd, 'w', encoding='utf-8') as target:
                    json.dump(data, target, ensure_ascii=True)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
                temporary = None
            except OSError:
                raise GatewayError(503, 'credentials_save_failed',
                                   'Cannot save credentials. Configure a writable persistent credential directory.') from None
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
        return {'status': 'saved', 'source': 'browser_json', 'persisted': True,
                'image_upload_verified_at_export': data['image']['upload_verified'],
                'upstream_validated': False}

    def logout(self):
        with self.lock:
            try:
                if self.path is not None:
                    self.path.unlink(missing_ok=True)
            except OSError:
                raise GatewayError(503, 'credentials_logout_failed',
                                   'Cannot remove saved credentials. Check credential directory permissions.') from None
        return {'status': 'logged_out', 'logged_in': False}


def read_signing_salt(path):
    try:
        if path is None:
            raise ValueError()
        with path.open('rb') as source:
            data = source.read(4097)
        if len(data) > 4096:
            raise ValueError()
        salt = json.loads(data.decode('utf-8-sig'))['salt']
        if not isinstance(salt, str) or not 1 <= len(salt) <= 256 or not all(33 <= ord(c) <= 126 for c in salt):
            raise ValueError()
        return salt
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise GatewayError(503, 'upstream_signing_required',
                           'Configure the separate site signing file for search-enabled requests.') from None


class BrowserCredentials:
    def __init__(self, store, signing_file=None, image=False):
        self.store = store
        self.signing_file = signing_file
        self.image = image

    def get_token(self):
        data = self.store.read()
        if data is not None:
            return data['image']['headers']['token'] if self.image else data['main']['headers']['X-Access-Token']
        raise GatewayError(503, 'upstream_login_required', 'Import browser credential JSON to log in to the gateway.')

    def get_signing(self, token):
        data = self.store.read()
        if data is None:
            raise GatewayError(503, 'upstream_login_required', 'Import browser credential JSON to log in to the gateway.')
        if token != data['main']['headers']['X-Access-Token']:
            raise GatewayError(503, 'upstream_credentials_changed', 'Credentials changed. Start a new request.')
        salt = data['signing']['salt'] if 'signing' in data else read_signing_salt(self.signing_file)
        return SigningCredentials(salt, data['main']['headers']['tenant-id'])
