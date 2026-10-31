"""Qwen SSE from model E534/search E1064, plus captured frontend branches.

Other model branches retain static-source evidence, not live verification.
No credentials or raw response text enter diagnostics or error messages.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .protocol import GatewayError


CHAT_URL = "https://genai.shanghaitech.edu.cn/htk/chat/start/chat"
ORIGIN = "https://genai.shanghaitech.edu.cn"
MAX_FRAME_BYTES = 1024 * 1024
_NEWLINE = re.compile(r"[\r\n]")
logger = logging.getLogger(__name__)


def _frame_shape(data: str) -> dict:
    """Bounded diagnostics: only fixed field names and types, never values."""
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        return {'format': 'done' if data.strip() == '[DONE]' else 'non_json'}
    if not isinstance(value, dict):
        return {'format': 'non_object'}
    fields = ('content', 'result', 'choices', 'output', 'data', 'delta',
              'message', 'other', 'isEnd', 'is_end', 'error', 'errMsg', 'code')
    shape = {key: type(value[key]).__name__ for key in fields if key in value}
    choices = value.get('choices')
    if isinstance(choices, list):
        shape['choice_count'] = len(choices)
        if choices and isinstance(choices[0], dict):
            choice = choices[0]
            for key in ('delta', 'message'):
                if isinstance(choice.get(key), dict):
                    shape[key + '_fields'] = [name for name in
                        ('content', 'reasoning_content', 'reasoning', 'tool_calls', 'function_call', 'refusal')
                        if name in choice[key]]
            finish = choice.get('finish_reason')
            shape['finish'] = finish if finish in ('stop', 'length', 'tool_calls', 'content_filter', None) else 'other'
    return shape


def _protocol_error() -> GatewayError:
    return GatewayError(
        502,
        "upstream_protocol_error",
        "The upstream stream does not match the demo's supported event format.",
    )


def _business_error_kind(item: dict) -> str:
    """Classify only fixed patterns; never return/log upstream message values.

    A site code 500 can also wrap authentication or invalid-input failures.
    Those must not become generation retries. Unknown non-500 errors retain
    their existing fail-closed behavior.
    """
    texts = []
    for key in ('message', 'msg', 'errMsg', 'error'):
        value = item.get(key)
        if isinstance(value, dict):
            nested_code = value.get('code')
            if nested_code in (401, 403):
                return 'authentication'
            if nested_code == 429:
                return 'quota'
            if nested_code in (400, 404, 413, 422):
                return 'input'
            texts.extend(v[:4096] for k, v in value.items()
                         if k in ('message', 'msg', 'type', 'code') and isinstance(v, str))
        elif isinstance(value, str):
            texts.append(value[:4096])
    text = ' '.join(texts).casefold()
    groups = {
        'authentication': ('unauthorized', 'authentication', 'forbidden', 'permission',
                           'credential', 'token expired', 'token has expired',
                           'invalid token', 'access denied', '登录', '认证', '令牌', '权限'),
        'quota': ('quota', 'rate limit', 'rate_limit', 'too many requests', 'balance',
                 'credit', '额度', '余额', '限流', '频率'),
        'input': ('invalid', 'unsupported', 'not supported', 'bad request',
                  'context length', 'context_length', 'max context', 'too long',
                  'too large', 'not found', 'validation', 'parameter', 'required field',
                  'maximum number of tokens', 'token limit', 'token_limit',
                  '参数', '格式', '不支持', '上下文', '过长', '超出', '不存在'),
    }
    for kind, patterns in groups.items():
        if any(pattern in text for pattern in patterns):
            return kind
    return 'server_500' if item.get('code') == 500 else 'unclassified'


async def _lines(
    response: httpx.Response, idle_timeout: float
) -> AsyncIterator[str]:
    """Split UTF-8 bytes into bounded lines, accepting CR, LF, and CRLF."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    # Do not request fixed-size chunks: HTTPX would buffer short SSE deltas
    # while waiting to fill a chunk, delaying token delivery.
    source = response.aiter_bytes().__aiter__()
    pending = ""
    skip_lf = False
    first_text = True
    exhausted = False
    while not exhausted:
        try:
            raw = await asyncio.wait_for(anext(source), timeout=idle_timeout)
        except StopAsyncIteration:
            exhausted = True
            raw = b""
        try:
            decoded = decoder.decode(raw, final=exhausted)
        except UnicodeError:
            raise _protocol_error() from None
        if first_text and decoded:
            first_text = False
            decoded = decoded.removeprefix("\ufeff")
        pending += decoded
        if skip_lf and pending:
            if pending.startswith("\n"):
                pending = pending[1:]
            skip_lf = False
        while True:
            match = _NEWLINE.search(pending)
            if match is None:
                if len(pending.encode("utf-8")) > MAX_FRAME_BYTES:
                    raise _protocol_error()
                break
            line = pending[: match.start()]
            if len(line.encode("utf-8")) > MAX_FRAME_BYTES:
                raise _protocol_error()
            separator = pending[match.start()]
            pending = pending[match.end() :]
            if separator == "\r":
                if pending.startswith("\n"):
                    pending = pending[1:]
                elif not pending:
                    skip_lf = True
            yield line
        if exhausted and pending:
            # A final line alone is not a completed SSE frame. The data parser
            # deliberately does not dispatch it without a blank separator.
            yield pending


async def _event_data(
    response: httpx.Response, idle_timeout: float
) -> AsyncIterator[str]:
    data_lines: list[str] = []
    frame_bytes = 0
    async for line in _lines(response, idle_timeout):
        frame_bytes += len(line.encode("utf-8")) + 1
        if frame_bytes > MAX_FRAME_BYTES:
            raise _protocol_error()
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
            data_lines = []
            frame_bytes = 0
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
        # event/id/retry are transport metadata, not model answer text. There
        # is deliberately no automatic reconnect or Last-Event-ID replay.


def _optional_string(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _protocol_error()
    return value


def _first_choice(container: Any) -> dict[str, Any]:
    if container is None:
        return {}
    if not isinstance(container, list):
        raise _protocol_error()
    if not container:
        return {}
    if not isinstance(container[0], dict):
        raise _protocol_error()
    # This demo exposes one completion only, never silently drops alternatives.
    if len(container) != 1:
        raise _protocol_error()
    return container[0]


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _protocol_error()
    return value


def _usage(value: Any) -> dict:
    """Only genuine nonnegative token counters, never summed across frames."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _protocol_error()
    result = {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        if key in value:
            if type(value[key]) is not int or value[key] < 0:
                raise _protocol_error()
            result[key] = value[key]
    for key in ('prompt_tokens_details', 'completion_tokens_details'):
        if key in value:
            details = _mapping(value[key])
            result[key] = {k: v for k, v in details.items()
                           if k in ('reasoning_tokens', 'cached_tokens', 'created_cache_tokens',
                                    'audio_tokens', 'accepted_prediction_tokens', 'rejected_prediction_tokens')
                           and type(v) is int and v >= 0}
    return result


def _normalise(
    data: str, root_ai_type: str, state: dict[str, Any]
) -> list[dict[str, Any]]:
    if not data:
        return []
    if data.strip() == "[DONE]":
        if state.get("has_output"):
            state['completed'] = True
            return [{"finish_reason": state.get("finish_reason", "stop")}]
        raise _protocol_error()
    try:
        item = json.loads(data)
    except (ValueError, RecursionError):
        raise _protocol_error() from None
    if not isinstance(item, dict):
        raise _protocol_error()
    if item.get("code") in (401, 403):
        raise GatewayError(
            503,
            "upstream_authentication_required",
            "The upstream credential must be renewed locally.",
        )
    if item.get("code") == 500 or item.get("error") or item.get("errMsg") or item.get("success") is False:
        kind = _business_error_kind(item)
        logger.warning('upstream_business_error root=%s category=%s site_code=%s',
                       root_ai_type, kind, '500' if item.get('code') == 500 else 'other')
        raise GatewayError(
            502, "upstream_error", "The upstream service reported a business error.",
            retryable=kind == 'server_500',
        )
    recognised = {
        "id", "object", "created", "model", "usage", "other", "promptTokens",
        "success", "code", "message", "timestamp", "system_fingerprint",
        "isEnd", "is_end", "choices", "output", "content", "result",
    }
    if root_ai_type == "azure":
        # Azure emits moderation metadata before/after text, sometimes with
        # no choices or with an empty delta. It is not answer content.
        recognised.update({"prompt_filter_results", "prompt_annotations",
                           "content_filter_results"})
    if item and not recognised.intersection(item):
        raise _protocol_error()

    choice = _first_choice(item.get("choices"))
    output = _mapping(item.get("output"))
    output_choice = _first_choice(output.get("choices"))
    delta = _mapping(choice.get("delta"))
    message = _mapping(choice.get("message"))
    output_delta = _mapping(output_choice.get("delta"))
    output_message = _mapping(output_choice.get("message"))
    if (
        delta.get("tool_calls") or delta.get("function_call")
        or message.get("tool_calls") or message.get("function_call")
        or output_delta.get("tool_calls") or output_delta.get("function_call")
        or output_message.get("tool_calls") or output_message.get("function_call")
    ):
        raise GatewayError(
            502, "unsupported_upstream_capability", "Tool output is not supported by this demo."
        )

    top_content = _optional_string(item.get("content"))
    result_content = _optional_string(item.get("result"))
    delta_content = _optional_string(delta.get("content"))
    message_content = _optional_string(output_message.get("content"))
    text = (top_content or result_content or delta_content
            or _optional_string(message.get("content"))
            or _optional_string(output_delta.get("content")) or message_content)
    reasoning = _optional_string(delta.get("reasoning_content")) or _optional_string(
        delta.get("reasoning")
    )

    metadata = {}
    if item.get('usage') is not None:
        metadata['usage'] = _usage(item['usage'])
    other = item.get("other")
    if other:
        if isinstance(other, str):
            try:
                parsed_other = json.loads(other)
            except (ValueError, RecursionError):
                raise _protocol_error() from None
            if not isinstance(parsed_other, dict):
                raise _protocol_error()
        elif isinstance(other, dict):
            parsed_other = other
        else:
            raise _protocol_error()
        metadata['upstream_metadata'] = {k: v for k, v in parsed_other.items()
            if k in ('promptTokens', 'totalTokens', 'completionTokens') and type(v) is int and v >= 0}
        # other.totalTokens differs from usage.total_tokens in observed site events.

    finish = choice.get("finish_reason") or output_choice.get("finish_reason")
    if finish not in (None, "", "stop", "length"):
        raise GatewayError(
            502,
            "unsupported_upstream_capability",
            "The upstream finish reason is not supported by this demo.",
        )
    if finish in ("stop", "length"):
        state["finish_reason"] = finish
    terminal: str | None = None
    if (
        finish in ("stop", "length")
        or item.get("isEnd") in (1, "1")
        or item.get("is_end") in (1, "1")
    ):
        terminal = state.get("finish_reason", "stop")
    elif root_ai_type == "azure" and other:
        terminal = state.get("finish_reason", "stop")

    events: list[dict[str, Any]] = []
    # Finish frames can carry the final text. Only later trailer frames must
    # not append a second answer after a logical completion.
    already_completed = state.get('completed', False)
    if reasoning and not already_completed:
        events.append({"reasoning_content": reasoning})
    if text and not already_completed:
        events.append({"content": text})
    if text or reasoning:
        state["has_output"] = "1"
    if metadata:
        events.append(metadata)
    if terminal:
        state['completed'] = True
        events.append({"finish_reason": terminal})
    return events


async def stream_upstream(
    client: httpx.AsyncClient,
    payload: dict,
    root_ai_type: str,
    token: str,
    idle_timeout: float = 90.0,
    tail_timeout: float = 1.0,
    timing=None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield normalised deltas, then one completion event; never retry POST.

    The caller owns the client and must close this async generator on client
    disconnect/cancellation. All failures contain fixed, safe messages only.
    """
    if not token or "\r" in token or "\n" in token:
        raise GatewayError(
            503, "upstream_authentication_required", "The upstream credential is not configured."
        )
    headers = {
        "X-Access-Token": token,
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/dashboard/analysis",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    started = time.monotonic()
    phase = 'response_headers'
    frame_count = text_chars = reasoning_chars = 0
    try:
        async with client.stream(
            "POST", CHAT_URL, json=payload, headers=headers, follow_redirects=False
        ) as response:
            phase = 'stream'
            if timing:
                timing.elapsed('upstream_headers_ms', started)
            if response.status_code in (401, 403):
                raise GatewayError(
                    503,
                    "upstream_authentication_required",
                    "The upstream credential must be renewed locally.",
                )
            if response.status_code == 429:
                raise GatewayError(
                    503, "upstream_rate_limited", "The upstream service is temporarily rate limited."
                )
            if response.status_code != 200:
                raise GatewayError(
                    502, "upstream_http_error", "The upstream service returned an unexpected HTTP status."
                )
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if mime != "text/event-stream":
                raise GatewayError(
                    502, "upstream_content_type_error", "The upstream response is not an event stream."
                )
            state: dict[str, Any] = {}
            recent_shapes = []
            frame_count = 0
            text_chars = 0
            reasoning_chars = 0
            completion = None
            tail_deadline = None
            metadata = {}
            frames = _event_data(response, idle_timeout)
            while True:
                try:
                    if tail_deadline is None:
                        data = await anext(frames)
                    else:
                        remaining = tail_deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        data = await asyncio.wait_for(anext(frames), remaining)
                except StopAsyncIteration:
                    break
                except (httpx.HTTPError, TimeoutError):
                    # A complete generation survives optional trailer timeout
                    # or a peer's abort. Before completion the same failure is
                    # propagated, never converted into a fabricated success.
                    if completion:
                        break
                    raise
                frame_count += 1
                if timing and frame_count == 1:
                    timing.elapsed('upstream_first_event_ms', started)
                recent_shapes.append(_frame_shape(data))
                recent_shapes = recent_shapes[-8:]
                for event in _normalise(data, root_ai_type, state):
                    if timing:
                        if event.get('content') and 'upstream_first_content_ms' not in timing.values:
                            timing.elapsed('upstream_first_content_ms', started)
                        if event.get('reasoning_content') and 'upstream_first_reasoning_ms' not in timing.values:
                            timing.elapsed('upstream_first_reasoning_ms', started)
                    text_chars += len(event.get('content', ''))
                    reasoning_chars += len(event.get('reasoning_content', ''))
                    if 'finish_reason' in event and not text_chars:
                        logger.warning('upstream_no_text root=%s frames=%d reasoning_chars=%d shapes=%s',
                                       root_ai_type, frame_count, reasoning_chars,
                                       json.dumps(recent_shapes, separators=(',', ':')))
                        if not reasoning_chars:
                            raise GatewayError(502, 'upstream_empty_stream',
                                'The upstream sent a completion signal without answer text or tool calls. Frame-shape diagnostics were recorded locally; no response text or credentials were saved.')
                    if "finish_reason" in event:
                        if timing and completion is None:
                            timing.elapsed('upstream_completion_ms', started)
                        completion = event
                        if tail_deadline is None:
                            tail_deadline = time.monotonic() + tail_timeout
                    elif 'content' in event or 'reasoning_content' in event:
                        yield event
                    else:
                        for key in ('usage', 'upstream_metadata'):
                            if key in event:
                                metadata.setdefault(key, {}).update(event[key])
            await frames.aclose()
            if completion:
                if timing:
                    timing.elapsed('upstream_total_ms', started)
                    if 'upstream_first_content_ms' in timing.values:
                        timing.values['upstream_output_ms'] = timing.values['upstream_completion_ms'] - timing.values['upstream_first_content_ms']
                        timing.values['upstream_trailer_ms'] = timing.values['upstream_total_ms'] - timing.values['upstream_completion_ms']
                if metadata:
                    yield metadata
                yield completion
                return
            raise GatewayError(
                502,
                "upstream_incomplete",
                "The upstream stream ended without a supported completion event.",
            )
    except GatewayError:
        raise
    except (httpx.TimeoutException, TimeoutError) as error:
        if isinstance(error, httpx.ConnectTimeout):
            phase = 'connect'
        elif isinstance(error, httpx.WriteTimeout):
            phase = 'request_write'
        elif isinstance(error, httpx.PoolTimeout):
            phase = 'connection_pool'
        logger.warning('upstream_timeout root=%s phase=%s elapsed_s=%.3f frames=%d text_chars=%d reasoning_chars=%d',
                       root_ai_type, phase, time.monotonic() - started, frame_count,
                       text_chars, reasoning_chars)
        message = {
            'connect': 'The connection to the upstream service timed out.',
            'request_write': 'Sending the request to the upstream service timed out.',
            'connection_pool': 'Waiting for a local upstream connection timed out.',
            'response_headers': 'The upstream did not send response headers before the read timeout. The request was not retried.',
            'stream': 'The upstream event stream was idle beyond the read timeout. The request was not retried.',
        }[phase]
        raise GatewayError(
            504, "upstream_timeout", message
        ) from None
    except httpx.HTTPError:
        raise GatewayError(
            502, "upstream_connection_error", "The upstream connection could not be completed."
        ) from None
