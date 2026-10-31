"""Bounded, in-memory local-to-upstream conversation IDs; no saved messages."""
import asyncio
import hashlib
import time
import weakref
from contextlib import asynccontextmanager

from .protocol import GatewayError, _new_conversation_id

TTL_SECONDS = 3600
MAX_CONVERSATIONS = 256


def validate_conversation_id(value):
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128
                              or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise GatewayError(400, 'invalid_conversation_id', 'conversation_id must contain 1 to 128 visible ASCII characters.')
    return value


class Conversations:
    def __init__(self):
        self.groups = {}
        self.locks = weakref.WeakValueDictionary()

    def group_for(self, token, model, local_id):
        validate_conversation_id(local_id)
        key = (hashlib.sha256(token.encode()).hexdigest(), model, local_id)
        now = time.monotonic()
        self.groups = {k: v for k, v in self.groups.items()
                       if now - v[1] < TTL_SECONDS or v[0] in self.locks}
        if key not in self.groups:
            if len(self.groups) >= MAX_CONVERSATIONS:
                raise GatewayError(429, 'conversation_capacity', 'Too many active local conversations. Wait for unused mappings to expire.')
            self.groups[key] = (_new_conversation_id(), now)
        group = self.groups[key][0]
        self.groups[key] = (group, now)
        return group

    @asynccontextmanager
    async def lock_group(self, group):
        lock = self.locks.get(group)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[group] = lock
        async with lock:
            yield
