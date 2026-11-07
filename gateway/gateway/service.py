from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import anyio

from .attachments import Attachments
from .audit import AuditStore
from .browser_credentials import BrowserCredentials, BrowserCredentialStore
from .conversations import Conversations
from .protocol import GatewayError, validate_conversation_id
from .site_api import delete_site_conversation
from .site_config import SiteCatalog, SiteConfig
from .tool_bridge import MAX_OUTPUT_CHARS
from .upstream import stream_upstream

logger = logging.getLogger(__name__)


def payload_has_images(payload):
    """Inspect the materialized wire payload, including historical images."""
    if payload.get("imageUrl") or payload.get("imageUrls"):
        return True
    return any(
        isinstance(message.get("content"), list)
        and any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in message["content"]
        )
        for message in payload.get("messages", [])
        if isinstance(message, dict)
    )


class Service:
    def __init__(self, settings, client):
        self.settings = settings
        self.client = client
        self.credential_store = BrowserCredentialStore(
            getattr(settings, "imported_credentials_file", None)
        )
        self.credentials = BrowserCredentials(
            self.credential_store, getattr(settings, "site_signing_file", None)
        )
        self.image_credentials = BrowserCredentials(self.credential_store, image=True)
        self.attachments = Attachments()
        journal = (
            self.credential_store.path.with_name("conversations.json")
            if self.credential_store.path
            else None
        )
        self.conversations = Conversations(journal)
        self.audit = AuditStore(journal.with_name("admin.sqlite3") if journal else None)
        self.started = time.monotonic()
        self.last_probe = self.probe_identity = None
        self.probe_lock = asyncio.Lock()
        self.deleting = False
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self.pending = 0
        self.running = 0
        self.catalog = SiteCatalog(client, self.credentials, settings.models)

    async def site_config(self, *, refresh=False):
        if getattr(self.settings, "dynamic_site_config", False):
            return await self.catalog.get(refresh=refresh)
        return SiteConfig(self.settings.models)

    @asynccontextmanager
    async def slot(self):
        if self.deleting:
            raise GatewayError(
                409,
                "conversation_deletion_in_progress",
                "Wait for conversation deletion to finish.",
            )
        if (
            self.pending
            >= self.settings.max_waiting_requests + self.settings.max_concurrent_requests
        ):
            raise GatewayError(429, "queue_full", "The local account queue is full.")
        self.pending += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self.semaphore.acquire(), timeout=self.settings.queue_timeout_seconds
                )
                acquired = True
                self.running += 1
            except TimeoutError:
                raise GatewayError(
                    429, "queue_timeout", "The local account queue timed out."
                ) from None
            yield
        finally:
            if acquired:
                self.running -= 1
                self.semaphore.release()
            self.pending -= 1

    async def events(
        self,
        payload,
        root_ai_type,
        attachment_plan=None,
        public_model=None,
        conversation_id=None,
        timing=None,
        bridge=None,
        config=None,
    ):
        waiting = time.monotonic()
        async with self.slot():
            if timing:
                timing.elapsed("queue_wait_ms", waiting)
            preparing = time.monotonic()
            token = await asyncio.to_thread(self.credentials.get_token)
            config = config or SiteConfig(self.settings.models)
            config.check_token(token)
            bound_group = None
            if conversation_id is not None:
                bound_group = self.conversations.group_for(
                    token, public_model or payload["aiType"], conversation_id
                )
                payload = {**payload, "chatGroupId": bound_group}
            if attachment_plan:
                needs_upload = any(
                    isinstance(ref, tuple) for refs in attachment_plan.values() for ref in refs
                )
                image_token = await self.image_token() if needs_upload else None
                if timing:
                    timing.elapsed("credentials_ms", preparing)
                preparing = time.monotonic()
                payload = await self.attachments.materialize(
                    self.client,
                    config.models,
                    public_model or payload["aiType"],
                    attachment_plan,
                    payload,
                    token,
                    image_token,
                    bound_group=bound_group,
                    image_base=config.image_base,
                    **({"timing": timing} if timing else {}),
                )
                if timing:
                    timing.elapsed("attachment_materialize_ms", preparing)
            waiting = time.monotonic()
            async with self.conversations.lock_group(payload["chatGroupId"]):
                payload = {
                    **payload,
                    "promptTokens": self.conversations.prompt_tokens.get(payload["chatGroupId"], 0),
                }
                if timing:
                    timing.elapsed("conversation_wait_ms", waiting)
                if attachment_plan and self.attachments.mixed_native_mode(attachment_plan):
                    yield {"attachment_mode": "website_file_ids_and_images"}
                generated = self.generate(
                    payload,
                    root_ai_type,
                    token,
                    bridge=bridge,
                    **({"timing": timing} if timing else {}),
                )
                try:
                    async for event in generated:
                        counter = event.get("upstream_metadata", {}).get("promptTokens")
                        if type(counter) is int and counter >= 0:
                            self.conversations.update_prompt_tokens(payload["chatGroupId"], counter)
                        yield event
                finally:
                    with anyio.CancelScope(shield=True):
                        await generated.aclose()

    async def generation_stream(self, payload, root_ai_type, token, timing=None):
        """Recover one empty or pre-output image-server failure for DeepSeek.

        Runs inside the existing account slot, conversation lock and total
        deadline. Attachments are already materialized and are never uploaded
        again. Only a classified site 500 may retry an image generation;
        partial output, timeout, auth, quota and input failures are not replayed.
        """
        for attempt in range(2):
            visible_output = False
            retry = False
            upstream = stream_upstream(
                self.client,
                payload,
                root_ai_type,
                token,
                idle_timeout=self.settings.read_timeout_seconds,
                **(
                    {
                        "header_timeout": max(
                            self.settings.read_timeout_seconds, self.settings.total_timeout_seconds
                        )
                    }
                    if payload_has_images(payload)
                    else {}
                ),
                **({"timing": timing} if timing else {}),
            )
            if timing:
                timing.count("upstream_attempts")
            try:
                async for event in upstream:
                    visible_output |= bool(
                        event.get("content")
                        or event.get("reasoning_content")
                        or event.get("tool_calls")
                        or event.get("finish_reason")
                    )
                    yield event
                return
            except GatewayError as error:
                empty = error.code == "upstream_empty_stream"
                image_server_error = (
                    error.code == "upstream_error"
                    and error.retryable
                    and payload_has_images(payload)
                )
                if (
                    attempt != 0
                    or payload.get("aiType") != "deepseek-pro"
                    or not (empty or image_server_error)
                    or visible_output
                ):
                    raise
                retry = "empty" if empty else "image_server_500"
            finally:
                with anyio.CancelScope(shield=True):
                    await upstream.aclose()
            if retry:
                if retry == "empty":
                    logger.warning("upstream_empty_recovery model=deepseek-pro retry=1")
                else:
                    logger.warning(
                        "upstream_image_recovery model=deepseek-pro reason=server_500 retry=1"
                    )
                await asyncio.sleep(0.25)

    async def generate(self, payload, root_ai_type, token, timing=None, bridge=None):
        sources_seen = False
        upstream = self.generation_stream(
            payload, root_ai_type, token, **({"timing": timing} if timing else {})
        )
        if bridge is not None:

            def retry(raw, error):
                corrected = bridge.correction_payload(payload, raw, error)
                return self.generation_stream(
                    corrected, root_ai_type, token, **({"timing": timing} if timing else {})
                )

            upstream = bridge.convert(upstream, retry=retry)
        deadline = time.monotonic() + self.settings.total_timeout_seconds
        size = 0
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GatewayError(
                        504, "upstream_timeout", "The upstream total time limit was reached."
                    )
                try:
                    event = await asyncio.wait_for(anext(upstream), timeout=remaining)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise GatewayError(
                        504, "upstream_timeout", "The upstream total time limit was reached."
                    ) from None
                size += len(event.get("content", "")) + len(event.get("reasoning_content", ""))
                if size > MAX_OUTPUT_CHARS:
                    raise GatewayError(
                        502,
                        "upstream_output_limit",
                        "The ShanghaiTech GenAI output size limit was reached.",
                    )
                if event.get("sources_status"):
                    sources_seen = True
                if event.get("finish_reason"):
                    if payload.get("netGo") and not sources_seen:
                        yield {
                            "sources_status": "failed",
                            "sources_error": "upstream_sources_missing",
                        }
                    yield event
                    return
                yield event
        finally:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()

    async def image_token(self):
        if self.image_credentials is None:
            raise GatewayError(
                503,
                "upstream_login_required",
                "Import browser credential JSON to log in to the gateway.",
            )
        return await asyncio.to_thread(self.image_credentials.get_token)


async def delete_conversations(service, ids):
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 256
        or any(validate_conversation_id(local) is None for local in ids)
    ):
        raise GatewayError(400, "invalid_conversation_ids", "Supply 1 to 256 conversation IDs.")
    ids = list(dict.fromkeys(ids))
    # No awaits between the admission check and gate. Chat/upload and credential
    # mutations are held out for the entire multi-group operation.
    if service.pending or service.deleting:
        raise GatewayError(
            409,
            "conversations_in_use",
            "Stop active requests and wait for queued work before deleting.",
        )
    service.deleting = True
    service.pending += 1
    try:
        token = await asyncio.to_thread(service.credentials.get_token)
        groups = service.conversations.deletion_plan(token, ids)
        if groups:
            tenant = await asyncio.to_thread(service.credentials.get_tenant, token)
            async with asyncio.timeout(service.settings.total_timeout_seconds):
                for group in groups:
                    await delete_site_conversation(service.client, group, token, tenant)
        # All confirmations are required; a partial failure retains the complete
        # ledger and the local session for explicit retry, never reports success.
        service.conversations.confirm_deleted(token, ids)
        for ref, record in list(service.attachments.records.items()):
            if record.group in groups:
                service.attachments.delete(ref)
        return {
            "object": "conversation.deletion",
            "conversation_ids": ids,
            "deleted": True,
            "deleted_groups": len(groups),
            "scope": "upstream_history_and_gateway_mapping",
        }
    except TimeoutError:
        raise GatewayError(
            504,
            "site_delete_timeout",
            "Conversation deletion did not finish. Local records were retained.",
        ) from None
    finally:
        service.pending -= 1
        service.deleting = False
