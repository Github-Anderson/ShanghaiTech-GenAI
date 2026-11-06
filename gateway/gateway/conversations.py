"""Conversation mappings, site counters and deletion ledger; no messages or credentials."""
import asyncio
import hashlib
import json
import os
import tempfile
import time
import weakref
from contextlib import asynccontextmanager

from .protocol import GatewayError, _new_conversation_id

TTL_SECONDS = 3600
MAX_CONVERSATIONS = 256
MAX_SAVED_GROUPS = 4096


def validate_conversation_id(value):
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128
                              or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise GatewayError(400, 'invalid_conversation_id', 'conversation_id must contain 1 to 128 visible ASCII characters.')
    return value


class Conversations:
    def __init__(self, path=None):
        self.path = path
        self.groups = {}
        self.history = {}
        self.deleted = set()
        self.prompt_tokens = {}
        self.locks = weakref.WeakValueDictionary()
        if path is not None:
            try:
                with path.open('rb') as source:
                    raw = source.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError()
                data = json.loads(raw)
                if data['version'] != 1 or len(data['groups']) > MAX_SAVED_GROUPS or len(data['deleted']) > MAX_SAVED_GROUPS:
                    raise ValueError()
                for owner, model, local, group, touched in data['groups']:
                    if (not isinstance(owner, str) or len(owner) != 64 or not isinstance(model, str)
                            or not isinstance(group, str) or len(group) != 21 or type(touched) not in (int, float)):
                        raise ValueError()
                    validate_conversation_id(local)
                    if local is None:
                        raise ValueError()
                    key = (owner, model, local)
                    self.history[group] = key
                    if time.time() - touched < TTL_SECONDS:
                        self.groups[key] = (group, touched)
                self.prompt_tokens = {g:n for g,n in data.get('prompt_tokens', {}).items()
                                      if g in self.history and type(n) is int and n >= 0}
                for owner, local in data['deleted']:
                    if not isinstance(owner, str) or len(owner) != 64 or validate_conversation_id(local) is None:
                        raise ValueError()
                    self.deleted.add((owner, local))
            except FileNotFoundError:
                pass
            except (OSError, ValueError, KeyError, TypeError, GatewayError):
                raise GatewayError(503, 'conversation_store_unreadable', 'The conversation deletion ledger cannot be read. Restore it before continuing.') from None

    def save(self):
        if self.path is None:
            return
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.conversations-', dir=self.path.parent)
            rows = [[*key, group, self.groups.get(key, (None, 0))[1]
                     if self.groups.get(key, (None,))[0] == group else 0]
                    for group, key in self.history.items()]
            with os.fdopen(fd, 'w', encoding='utf8') as out:
                json.dump({'version': 1, 'groups': rows, 'deleted': sorted(self.deleted), 'prompt_tokens': self.prompt_tokens}, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError:
            raise GatewayError(503, 'conversation_store_unwritable', 'The conversation deletion ledger cannot be saved.') from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def group_for(self, token, model, local_id):
        validate_conversation_id(local_id)
        owner = hashlib.sha256(token.encode()).hexdigest()
        key = (owner, model, local_id)
        now = time.time()
        self.groups = {k: v for k, v in self.groups.items()
                       if now - v[1] < TTL_SECONDS or v[0] in self.locks}
        if key not in self.groups:
            if len(self.groups) >= MAX_CONVERSATIONS or len(self.history) >= MAX_SAVED_GROUPS:
                raise GatewayError(429, 'conversation_capacity', 'Conversation mapping capacity reached. Delete unused conversations first.')
            group = _new_conversation_id()
            self.groups[key] = (group, now)
            self.history[group] = key
            self.deleted.discard((owner, local_id))
            try:
                self.save()
            except BaseException:
                self.groups.pop(key, None)
                self.history.pop(group, None)
                raise
        group = self.groups[key][0]
        self.groups[key] = (group, now)
        return group

    def update_prompt_tokens(self, group, count):
        if group in self.history:
            old = self.prompt_tokens.get(group)
            self.prompt_tokens[group] = count
            try:
                self.save()
            except BaseException:
                if old is None:
                    self.prompt_tokens.pop(group, None)
                else:
                    self.prompt_tokens[group] = old
                raise

    def deletion_plan(self, token, ids):
        owner = hashlib.sha256(token.encode()).hexdigest()
        known = {key[2] for key in self.history.values() if key[0] == owner}
        known.update(local for fingerprint, local in self.deleted if fingerprint == owner)
        if any(local not in known for local in ids):
            raise GatewayError(404, 'conversation_mapping_missing', 'No complete mapping exists for this credential. Older or expired-login conversations must be deleted on the website; local deletion was not confirmed.')
        return [group for group, key in self.history.items() if key[0] == owner and key[2] in ids]

    def confirm_deleted(self, token, ids):
        owner = hashlib.sha256(token.encode()).hexdigest()
        old = (self.groups.copy(), self.history.copy(), self.deleted.copy(), self.prompt_tokens.copy())
        self.groups = {k: v for k, v in self.groups.items() if not (k[0] == owner and k[2] in ids)}
        self.history = {g: k for g, k in self.history.items() if not (k[0] == owner and k[2] in ids)}
        self.prompt_tokens = {g:n for g,n in self.prompt_tokens.items() if g in self.history}
        self.deleted.update((owner, local) for local in ids)
        # Idempotency receipts are bounded; expired receipts never authorize a remote delete.
        self.deleted = set(sorted(self.deleted)[-MAX_SAVED_GROUPS:])
        try:
            self.save()
        except BaseException:
            self.groups, self.history, self.deleted, self.prompt_tokens = old
            raise

    @asynccontextmanager
    async def lock_group(self, group):
        lock = self.locks.get(group)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[group] = lock
        async with lock:
            yield
