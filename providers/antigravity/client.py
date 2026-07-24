"""Google Cloud Code Antigravity provider (Gemini generateContent API).

This provider communicates with Google's Cloud Code backend at
``cloudcode-pa.googleapis.com``, using Gemini's native generateContent API.
It authenticates via Google OAuth (same credentials as Google's Antigravity CLI).

The backend multiplexes multiple upstreams (Gemini-native, Gemini-to-Anthropic
translation, OpenAI-compatible bridges) behind a single endpoint, so schema
normalization is applied per-model.

Ported from jcode's ``jcode-provider-antigravity-runtime`` crate (Rust).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from loguru import logger

from providers.base import BaseProvider, ProviderConfig
from providers.exceptions import ProviderError

from core.anthropic.native_messages_request import dump_raw_messages_request

from .auth import _antigravity_headers, load_or_refresh_tokens
from .catalog import (
    DEFAULT_FALLBACK_MODEL,
    AVAILABLE_MODELS,
    CatalogModel,
    CatalogSnapshot,
    catalog_is_stale,
    catalog_model_detail,
    load_persisted_catalog,
    persist_catalog,
    remap_unsupported_model,
)
from .request import (
    build_force_function_call_request,
    build_generate_content_request,
)
from .schema import antigravity_compatible_schema

# generateContent endpoint
_GENERATE_URL = "https://cloudcode-pa.googleapis.com/v1internal:generateContent"

_MAX_MALFORMED_RETRIES = 2

# Normal terminal reasons -- not retried
_TERMINAL_REASONS = frozenset(
    {"STOP", "MAX_TOKENS", "FINISH_REASON_UNSPECIFIED", ""}
)


class AntigravityProvider(BaseProvider):
    """Google Cloud Code Antigravity provider (Gemini generateContent API).

    Authenticates via Google OAuth. Communicates with the Cloud Code backend
    using Gemini's native generateContent format. Converts responses back to
    Anthropic SSE format for the proxy pipeline.
    """

    def __init__(self, config: ProviderConfig, *, model: str = "default"):
        # Antigravity doesn't use API keys -- OAuth tokens are loaded from disk.
        # The model is passed explicitly from settings.antigravity_model.
        super().__init__(config)
        self._model = model or "default"
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout=config.http_read_timeout,
                connect=config.http_connect_timeout,
                read=config.http_read_timeout,
                write=config.http_write_timeout,
                pool=config.http_connect_timeout,
            ),
        )
        # In-memory catalog cache
        self._fetched_catalog: list[CatalogModel] = []
        self._backend_default_model: str | None = None
        self._seed_cached_catalog()

    def _seed_cached_catalog(self) -> None:
        """Load persisted catalog into memory on startup."""
        catalog = load_persisted_catalog()
        if catalog is None:
            return
        if catalog_is_stale(catalog.fetched_at_rfc3339):
            logger.info(
                "Loaded stale persisted Antigravity model catalog; a refresh will update it"
            )
        self._backend_default_model = catalog.default_model_id
        self._fetched_catalog = catalog.models

    def _resolve_model_for_request(self, model: str) -> str:
        """Resolve the model alias ``default`` to a real backend model id."""
        trimmed = model.strip()
        if trimmed and trimmed != "default":
            return remap_unsupported_model(trimmed)

        if self._backend_default_model:
            bd = self._backend_default_model.strip()
            if bd and bd != "default":
                return bd

        # Prefer a Gemini model (works reliably with tool use)
        for cm in self._fetched_catalog:
            if cm.available and cm.id != "default" and cm.id.startswith("gemini-"):
                return cm.id
        for cm in self._fetched_catalog:
            if cm.available and cm.id != "default":
                return cm.id

        return DEFAULT_FALLBACK_MODEL

    async def cleanup(self) -> None:
        await self._http.aclose()

    async def list_model_ids(self) -> frozenset[str]:
        # Return catalog models if available, otherwise known defaults
        if self._fetched_catalog:
            return frozenset(
                m.id for m in self._fetched_catalog if m.available
            )
        # Try fetching live catalog from the backend
        try:
            from .catalog import fetch_catalog_snapshot

            snapshot = await fetch_catalog_snapshot()
            self._fetched_catalog = snapshot.models
            self._backend_default_model = snapshot.default_model_id
            if self._fetched_catalog:
                return frozenset(
                    m.id for m in self._fetched_catalog if m.available
                )
        except Exception:
            pass
        return frozenset(AVAILABLE_MODELS)

    async def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        """Stream response in Anthropic SSE format.

        Converts the Gemini generateContent response back to Anthropic SSE
        format that Claude Code expects.
        """
        try:
            tokens = load_or_refresh_tokens()
        except RuntimeError as exc:
            raise ProviderError(str(exc)) from exc

        # Serialize Pydantic request to dict (messages become dicts, not Pydantic objects)
        body = dump_raw_messages_request(request)

        # Extract Anthropic Messages from the serialized body
        messages = body.get("messages", []) or []
        system_text = body.get("system", "")
        if isinstance(system_text, list):
            system_text = "\n".join(
                b.get("text", "") for b in system_text if isinstance(b, dict) and b.get("type") == "text"
            )
        system = str(system_text) if system_text else ""
        tools_raw = body.get("tools", None)
        tools = [t for t in tools_raw] if tools_raw else None
        model_name = body.get("model", "") or self._model or "default"
        resolved_model = self._resolve_model_for_request(model_name)
        project = tokens.project_id or ""

        # Convert to Gemini format
        gemini_request = build_generate_content_request(
            messages=messages,
            system=system,
            tools=tools,
            model=resolved_model,
            project=project,
        )

        # Apply per-model schema normalization to tool parameters
        if tools and gemini_request.get("request", {}).get("tools"):
            for tool_group in gemini_request["request"]["tools"]:
                for decl in tool_group.get("functionDeclarations", []):
                    if "parameters" in decl:
                        decl["parameters"] = antigravity_compatible_schema(
                            decl["parameters"], resolved_model
                        )

        # Send request with retries for MALFORMED_FUNCTION_CALL
        response = await self._generate_content(
            tokens.access_token,
            gemini_request,
            resolved_model,
        )

        # Retry loop for Gemini-3 MALFORMED_FUNCTION_CALL
        retries = 0
        while _is_retryable_empty_turn(response) and retries < _MAX_MALFORMED_RETRIES:
            retries += 1
            logger.debug(
                "ANTIGRAVITY_RETRY: MALFORMED_FUNCTION_CALL retry {}/{}",
                retries,
                _MAX_MALFORMED_RETRIES,
            )
            # Force function calling mode ANY on retry
            force_request = build_force_function_call_request(
                messages=messages,
                system=system,
                tools=tools,
                model=resolved_model,
                project=project,
            )
            if tools and force_request.get("request", {}).get("tools"):
                for tool_group in force_request["request"]["tools"]:
                    for decl in tool_group.get("functionDeclarations", []):
                        if "parameters" in decl:
                            decl["parameters"] = antigravity_compatible_schema(
                                decl["parameters"], resolved_model
                            )
            response = await self._generate_content(
                tokens.access_token,
                force_request,
                resolved_model,
            )

        # Convert Gemini response to Anthropic SSE events
        for event in _gemini_response_to_sse(response):
            yield event

    async def _generate_content(
        self,
        access_token: str,
        gemini_request: dict[str, Any],
        resolved_model: str,
    ) -> dict[str, Any]:
        """Send a generateContent request to the Antigravity backend."""
        headers = _antigravity_headers(access_token)
        project = gemini_request.get("project", "")

        try:
            resp = await self._http.post(
                _GENERATE_URL,
                headers={
                    **headers,
                    "x-goog-request-params": f"project={project}",
                },
                json=gemini_request,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Antigravity generateContent request failed: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise ProviderError(
                f"Antigravity generateContent failed (HTTP {resp.status_code}): "
                f"{resp.text[:500]}"
            )

        return resp.json()

    def _extract_system(self, request: Any) -> str:
        """Extract system prompt from the Anthropic request."""
        system = getattr(request, "system", None)
        if system is None:
            return ""
        if isinstance(system, str):
            return system
        if isinstance(system, list):
            parts = []
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(parts)
        return str(system)


def _is_retryable_empty_turn(response: dict[str, Any]) -> bool:
    """Whether the response is a Gemini-3 MALFORMED_FUNCTION_CALL that should be retried."""
    candidates = (
        response.get("response", {})
        .get("candidates", [])
    )
    if not candidates:
        return False
    candidate = candidates[0]
    content = candidate.get("content", {})
    parts = content.get("parts", [])
    has_output = any(
        p.get("functionCall") or (p.get("text") or "").strip()
        for p in parts
    )
    if has_output:
        return False
    finish_reason = (candidate.get("finishReason") or "").upper()
    return finish_reason not in _TERMINAL_REASONS


def _gemini_response_to_sse(response: dict[str, Any]) -> AsyncIterator[str]:
    """Convert a Gemini generateContent response to Anthropic SSE format.

    Yields SSE event lines that Claude Code can consume.
    """
    candidates = (
        response.get("response", {})
        .get("candidates", [])
    )

    # Emit usage metadata
    usage = response.get("response", {}).get("usageMetadata", {})
    input_tokens = usage.get("promptTokenCount", 0)
    output_tokens = usage.get("candidatesTokenCount", 0)

    # message_start
    yield _sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        },
    )

    if not candidates:
        # No candidates -- emit error
        yield _sse_event(
            "error",
            {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "Antigravity returned no candidates",
                },
            },
        )
        yield _sse_event("message_stop", {"type": "message_stop"})
        return

    candidate = candidates[0]
    content = candidate.get("content", {})
    parts = content.get("parts", [])
    stop_reason_raw = candidate.get("finishReason", "STOP")
    stop_reason = _map_stop_reason(stop_reason_raw)
    produced_output = False

    for part in parts:
        # Text content
        text = part.get("text")
        if text and text.strip():
            produced_output = True
            yield _sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            yield _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                },
            )
            yield _sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            )

        # Function call
        fc = part.get("functionCall")
        if fc:
            produced_output = True
            call_id = fc.get("id") or str(uuid.uuid4())
            name = fc.get("name", "tool")
            args = fc.get("args", {})
            args_str = json.dumps(args) if isinstance(args, dict) else str(args)

            yield _sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": {},
                    },
                },
            )
            yield _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": args_str,
                    },
                },
            )
            yield _sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": 1},
            )

    # Check for abnormal empty turn
    if not produced_output:
        finish_reason_upper = (stop_reason_raw or "").upper()
        if finish_reason_upper not in _TERMINAL_REASONS:
            detail = candidate.get("finishMessage", "")
            yield _sse_event(
                "error",
                {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": (
                            f"Antigravity returned no usable output "
                            f"(finish_reason={stop_reason_raw})"
                            + (f": {detail[:300]}" if detail else "")
                        ),
                    },
                },
            )

    # message_delta with stop_reason and usage
    yield _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
            },
            "usage": {"output_tokens": output_tokens},
        },
    )

    yield _sse_event("message_stop", {"type": "message_stop"})


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a dict as an SSE event line."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _map_stop_reason(reason: str | None) -> str:
    """Map Gemini finishReason to Anthropic stop_reason."""
    if not reason:
        return "end_turn"
    r = reason.upper()
    if r == "STOP":
        return "end_turn"
    if r == "MAX_TOKENS":
        return "max_tokens"
    if r == "MALFORMED_FUNCTION_CALL":
        return "tool_use"
    return "end_turn"
