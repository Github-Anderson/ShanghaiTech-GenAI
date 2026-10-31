"""Compatibility mapping after tools and attachments have been normalized.

This module performs no network or credential operations.
Service binds an optional local conversation_id after conversion. Without it,
requests remain stateless; callers always supply explicit history in messages.
"""

from __future__ import annotations

import json
import secrets
from typing import Any


MAX_INPUT_BYTES = 1024 * 1024
MAX_HISTORY_MESSAGES = 128

# Public random-ID alphabet from the captured e1bd JavaScript module (E251).
# This is an alphabet, not an existing conversation ID or authentication value.
_ID_ALPHABET = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"


class GatewayError(Exception):
    """An intentional error whose message is safe to return to the caller.

    Callers must use fixed gateway-owned messages. Never instantiate this class
    with exception text, upstream response bodies, headers, or credentials.
    """

    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        # Internal transport classification only; never copied to public JSON.
        self.retryable = retryable
        super().__init__(message)


def _invalid(code: str, message: str) -> GatewayError:
    return GatewayError(400, code, message)


def _new_conversation_id() -> str:
    # The browser indexes 21 random bytes in reverse using byte & 63.
    return "".join(_ID_ALPHABET[value & 63] for value in reversed(secrets.token_bytes(21)))


def resolve_model_options(request: dict, profile: dict) -> tuple[bool, bool]:
    """Resolve a per-request snapshot; never change shared model configuration.

    Partial option objects inherit configured defaults.
    """
    search = profile.get('net_go', False)
    thinking = profile.get('thinking', False)
    if type(search) is not bool or type(thinking) is not bool:
        raise GatewayError(503, 'configuration_error', 'Model option defaults must be boolean.')
    if 'shanghaitech' in request:
        options = request['shanghaitech']
        if not isinstance(options, dict) or set(options) - {'search', 'thinking'} or any(type(v) is not bool for v in options.values()):
            raise _invalid('invalid_model_options', 'shanghaitech accepts only boolean search and thinking fields.')
        search = options.get('search', search)
        thinking = options.get('thinking', thinking)
    if search and not profile.get('supports_search', False):
        raise _invalid('search_not_supported', 'Search is not enabled for this model.')
    if thinking and not profile.get('supports_thinking', False):
        raise _invalid('thinking_not_supported', 'Thinking is not enabled for this model.')
    if thinking and not profile.get('enable_thinking', False):
        raise GatewayError(503, 'configuration_error', 'The model has no configured Thinking transport.')
    return search, thinking


def build_upstream_payload(
    request: dict[str, Any], models: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], str]:
    """Convert the supported Chat Completions subset into captured site fields.

    ``models`` is a trusted local configuration mapping model IDs to
    ``root_ai_type``, ``max_tokens``, and ``enable_thinking``. The model's
    configured output limit bounds supported caller overrides.
    System messages have static source evidence, but remain unverified online.
    HTML-like content is preserved, not stripped as the browser UI may do.
    """
    if not isinstance(request, dict):
        raise _invalid("invalid_request", "The request must be a JSON object.")

    try:
        encoded_size = len(
            json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError, UnicodeError):
        raise _invalid("invalid_request", "The request must contain valid UTF-8 JSON.") from None
    if encoded_size > MAX_INPUT_BYTES:
        raise GatewayError(413, "request_too_large", "The request exceeds the demo input limit.")

    supported_fields = {"model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens", "tools", "tool_choice", "store", "shanghaitech", "conversation_id"}
    if set(request) - supported_fields:
        raise _invalid("unsupported_parameter", "The request contains an unsupported parameter.")
    from .conversations import validate_conversation_id
    validate_conversation_id(request.get('conversation_id'))
    if "tools" in request and request["tools"] != []:
        raise _invalid("tools_not_supported", "This source supports text only. Disable DSH tools for this provider; tool calls are not silently discarded.")
    if "tool_choice" in request and request["tool_choice"] != "none":
        raise _invalid("tools_not_supported", "Only tool_choice=none is supported by this text-only source.")
    if "store" in request and request["store"] is not False:
        raise _invalid("store_not_supported", "Only store=false is accepted. The upstream website may retain its own chat history.")
    if "max_tokens" in request and "max_completion_tokens" in request:
        raise _invalid("invalid_token_limit", "Specify only one output limit field.")

    model_id = request.get("model")
    if not isinstance(model_id, str) or not model_id or model_id not in models:
        raise _invalid("unknown_model", "Select a model returned by this gateway's model list.")

    stream = request.get("stream", False)
    if not isinstance(stream, bool):
        raise _invalid("invalid_stream", "The stream field must be a boolean.")
    if "stream_options" in request:
        options = request["stream_options"]
        if (
            not isinstance(options, dict)
            or set(options) - {"include_usage"}
            or ("include_usage" in options and type(options["include_usage"]) is not bool)
        ):
            raise _invalid(
                "unsupported_stream_options",
                "stream_options accepts only a boolean include_usage field.",
            )
        if not stream:
            raise _invalid("invalid_stream_options", "Stream options require stream=true.")

    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise _invalid("invalid_messages", "Messages must be a nonempty array of text messages.")
    if len(messages) - 1 > MAX_HISTORY_MESSAGES:
        raise _invalid(
            "history_too_long",
            "The gateway accepts at most 128 historical messages before the current user message.",
        )

    normalized: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise _invalid(
                "invalid_message",
                "Each message must contain only role and plain-text content.",
            )
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant", "system"):
            raise _invalid("unsupported_role", "Only user, assistant, and initial system roles are supported.")
        if role == "system" and index != 0:
            raise _invalid("invalid_system_message", "A system message is allowed only as the first message.")
        if isinstance(content, list):
            if not content or not all(isinstance(part, dict) and set(part) == {"type", "text"} and part.get("type") == "text" and isinstance(part.get("text"), str) for part in content):
                raise _invalid("unsupported_content", "Only plain text content blocks are supported.")
            content = "\n".join(part["text"] for part in content)
        if not isinstance(content, str) or not content.strip():
            raise _invalid("invalid_content", "Message content must be a nonempty plain-text string.")
        normalized.append({"role": role, "content": content})

    if normalized[-1]["role"] != "user":
        raise _invalid("invalid_last_message", "The final message must have the user role.")

    profile = models[model_id]
    if not isinstance(profile, dict):
        raise GatewayError(503, "configuration_error", "The selected model profile is not configured correctly.")
    root_ai_type = profile.get("root_ai_type")
    max_tokens = profile.get("max_tokens")
    enable_thinking = profile.get("enable_thinking", False)
    if (
        root_ai_type not in ("azure", "xinference")
        or type(max_tokens) is not int
        or max_tokens <= 0
        or not isinstance(enable_thinking, bool)
    ):
        raise GatewayError(503, "configuration_error", "The selected model profile is not configured correctly.")

    requested_limit = request.get("max_tokens", request.get("max_completion_tokens", max_tokens))
    if type(requested_limit) is not int or not 1 <= requested_limit <= max_tokens:
        raise _invalid("invalid_token_limit", "The output limit must be a positive integer within the configured model limit.")
    search, thinking = resolve_model_options(request, profile)
    payload: dict[str, Any] = {
        "chatInfo": normalized[-1]["content"],
        "messages": normalized[:-1],
        "type": "3",
        "stream": True,
        "aiType": profile.get("upstream_model", model_id),
        "aiSecType": "1",
        "chatGroupId": _new_conversation_id(),
        "promptTokens": 0,
        "imageUrl": "",
        "imageUrls": [],
        "width": "",
        "height": "",
        "rootAiType": root_ai_type,
        "maxToken": requested_limit,
        "netGo": search,
    }
    if enable_thinking:
        if not isinstance(thinking, bool):
            raise GatewayError(503, "configuration_error", "The thinking profile must be boolean.")
        payload["thinking"] = thinking
    return payload, model_id


def make_chunk(
    response_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    """Build one downstream JSON chunk; SSE framing is the caller's job."""
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {"index": 0, "delta": dict(delta), "finish_reason": finish_reason}
        ],
    }


def make_completion(
    response_id: str,
    created: int,
    model: str,
    content: str,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """Build an aggregated response without inventing token-usage statistics."""
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
    }


def error_body(error: GatewayError) -> dict[str, Any]:
    """Return only the fixed safe error; never include an exception cause."""
    if error.status_code == 401:
        error_type = "authentication_error"
    elif error.status_code == 429:
        error_type = "rate_limit_error"
    elif error.status_code < 500:
        error_type = "invalid_request_error"
    else:
        error_type = "server_error"
    return {
        "error": {
            "message": error.message,
            "type": error_type,
            "param": None,
            "code": error.code,
        }
    }
