import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager

import anyio
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .audit import record_error
from .protocol import GatewayError, error_body, make_chunk, make_completion


def sse(data):
    return "data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"


def collect_metadata(target, event):
    if "usage" in event:
        target["usage"] = event["usage"]
    for key in (
        "sources",
        "sources_status",
        "sources_error",
        "search_progress",
        "upstream_metadata",
        "tool_routing",
        "attachment_mode",
        "timings",
    ):
        if key in event:
            target.setdefault("shanghaitech", {})[key] = event[key]


async def close_events(events):
    with anyio.CancelScope(shield=True):
        await events.aclose()


async def watch_disconnect(request, owner, stopped):
    """Aggregation has no StreamingResponse disconnect listener of its own."""
    while not stopped.is_set():
        if await request.is_disconnected():
            if not stopped.is_set():
                owner.cancel()
            return
        await asyncio.sleep(0.25)


@asynccontextmanager
async def disconnect_watcher(request):
    stopped = asyncio.Event()
    watcher = asyncio.create_task(watch_disconnect(request, asyncio.current_task(), stopped))
    try:
        yield
    finally:
        stopped.set()
        watcher.cancel()
        with anyio.CancelScope(shield=True):
            try:
                await watcher
            except asyncio.CancelledError:
                pass


async def chat_response(request, data, events, model):
    response_id = "chatcmpl-" + secrets.token_hex(12)
    created = int(time.time())
    if not data.get("stream", False):
        text, reasoning, calls, finish = [], [], [], None
        metadata = {}
        try:
            async with disconnect_watcher(request):
                async for event in events:
                    collect_metadata(metadata, event)
                    if "content" in event:
                        text.append(event["content"])
                    if "reasoning_content" in event:
                        reasoning.append(event["reasoning_content"])
                    if "tool_calls" in event:
                        calls.extend(event["tool_calls"])
                    if event.get("finish_reason"):
                        finish = event["finish_reason"]
                if finish is None:
                    raise GatewayError(
                        502,
                        "upstream_incomplete",
                        "The upstream stream ended without a recognized completion signal.",
                    )
                result = make_completion(
                    response_id, created, model, "".join(text), finish_reason=finish
                )
                result.update(metadata)
                if reasoning:
                    result["choices"][0]["message"]["reasoning_content"] = "".join(reasoning)
                if calls:
                    result["choices"][0]["message"].update(
                        content=None,
                        tool_calls=[
                            {k: v for k, v in call.items() if k != "index"} for call in calls
                        ],
                    )
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
        finally:
            await close_events(events)

    # Before StreamingResponse starts, it cannot observe client disconnects.
    # Cancel prefetch too, otherwise a cancelled DSH turn holds an account slot
    # until the upstream finishes (up to the total request timeout).
    async with disconnect_watcher(request):
        try:
            first = await anext(events)
        except StopAsyncIteration:
            await close_events(events)
            raise GatewayError(
                502, "upstream_incomplete", "The upstream returned no recognized events."
            ) from None
        except BaseException:
            await close_events(events)
            raise

    async def output():
        try:
            yield sse(make_chunk(response_id, created, model, {"role": "assistant"}))
            event = first
            metadata = {}
            while True:
                collect_metadata(metadata, event)
                delta = {
                    key: event[key]
                    for key in ["content", "reasoning_content", "tool_calls"]
                    if key in event
                }
                if delta:
                    yield sse(make_chunk(response_id, created, model, delta))
                if "search_progress" in event:
                    progress = make_chunk(response_id, created, model, {})
                    progress.update(
                        choices=[], shanghaitech={"search_progress": event["search_progress"]}
                    )
                    yield sse(progress)
                if event.get("finish_reason"):
                    final = make_chunk(
                        response_id, created, model, {}, finish_reason=event["finish_reason"]
                    )
                    if "shanghaitech" in metadata:
                        final["shanghaitech"] = metadata["shanghaitech"]
                    yield sse(final)
                    if data.get("stream_options", {}).get("include_usage") and "usage" in metadata:
                        usage = make_chunk(response_id, created, model, {})
                        usage.update(choices=[], usage=metadata["usage"])
                        yield sse(usage)
                    yield "data: [DONE]\n\n"
                    return
                try:
                    event = await anext(events)
                except StopAsyncIteration:
                    raise GatewayError(
                        502,
                        "upstream_incomplete",
                        "The upstream stream ended without a recognized completion signal.",
                    ) from None
        except GatewayError as error:
            record_error(request, error)
            yield sse(error_body(error))
        except asyncio.CancelledError:
            raise
        except Exception:
            error = GatewayError(
                502, "upstream_stream_error", "The upstream stream could not be completed."
            )
            record_error(request, error)
            yield sse(error_body(error))
        finally:
            await close_events(events)

    return StreamingResponse(
        output(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(close_events, events),
    )
