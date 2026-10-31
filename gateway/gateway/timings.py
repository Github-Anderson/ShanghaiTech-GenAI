"""Request-local monotonic image timings; no prompts, paths or credentials."""
import json
import logging
import secrets
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


class ImageTimings:
    def __init__(self, started=None):
        self.started = time.monotonic() if started is None else started
        self.request_id = secrets.token_hex(8)
        self.values = {}

    def elapsed(self, key, since, *, add=False):
        value = (time.monotonic() - since) * 1000
        self.values[key] = self.values.get(key, 0) + value if add else value

    def count(self, key, amount=1):
        self.values[key] = self.values.get(key, 0) + amount

    def snapshot(self):
        return {'request_id': self.request_id, 'unit': 'ms',
                **{key: round(value, 3) for key, value in self.values.items()}}


async def timed_output(events, timing):
    if timing is None:
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()
        return
    outcome = 'cancelled'
    try:
        async for event in events:
            if event.get('content') and 'gateway_first_text_ms' not in timing.values:
                timing.elapsed('gateway_first_text_ms', timing.started)
            if event.get('finish_reason'):
                outcome = 'complete'
                timing.elapsed('gateway_total_ms', timing.started)
                yield {'timings': timing.snapshot()}
            yield event
    except Exception:
        outcome = 'failed'
        raise
    finally:
        timing.elapsed('gateway_total_ms', timing.started)
        logger.info('image_request_timing outcome=%s %s', outcome,
                    json.dumps(timing.snapshot(), separators=(',', ':')))
        await events.aclose()
