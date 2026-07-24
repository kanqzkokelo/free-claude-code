"""Build Gemini generateContent requests from Anthropic Messages format.

The Antigravity backend uses Gemini's native ``generateContent`` / ``streamGenerateContent``
API, not OpenAI-compatible chat completions. This module converts the Anthropic Messages
protocol (as received from Claude Code) into the Gemini request format.

Ported from jcode's ``jcode-provider-gemini`` crate (Rust).
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

# Gemini-specific JSON Schema keywords the generateContent endpoint rejects.
_GEMINI_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "definitions",
        "$comment",
    }
)

# Prevention guidance appended to system prompt when tools are advertised.
_GEMINI_FUNCTION_CALL_GUARD = (
    "\n\n## Function calling\n"
    "- When you call a tool, emit a native function call, not code. Never write "
    "Python (or any language) that calls the tool, and never wrap a call in "
    "print(...) or a code block.\n"
    "- Use the function name exactly as defined. Do not prepend `default_api.` "
    "or any other namespace to the function name."
)


def gemini_compatible_schema(schema: Any) -> Any:
    """Strip unsupported JSON Schema keywords for the Gemini API."""
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for key, value in schema.items():
            if key in _GEMINI_UNSUPPORTED_SCHEMA_KEYS:
                continue
            if key == "const":
                out["enum"] = [gemini_compatible_schema(value)]
            else:
                out[key] = gemini_compatible_schema(value)
        return out
    if isinstance(schema, list):
        return [gemini_compatible_schema(item) for item in schema]
    return schema


def _build_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Convert Anthropic tool definitions to Gemini functionDeclarations format."""
    if not tools:
        return None

    declarations = []
    for tool in tools:
        input_schema = tool.get("input_schema") or tool.get("inputSchema") or {}
        declarations.append(
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": gemini_compatible_schema(input_schema),
            }
        )
    return [{"functionDeclarations": declarations}]


def _build_system_instruction(
    system: str, has_tools: bool
) -> dict[str, Any] | None:
    """Build the Gemini systemInstruction from the system prompt."""
    text = system.strip()
    if not text:
        return None
    if has_tools:
        text += _GEMINI_FUNCTION_CALL_GUARD
    return {
        "role": "user",
        "parts": [{"text": text}],
    }


def _convert_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Anthropic Messages to Gemini contents format.

    Gemini uses 'user'/'model' roles. Function calls and responses are
    embedded as parts within the content.
    """
    contents: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role", "user")
        gemini_role = "user" if role == "user" else "model"

        content = message.get("content")
        parts: list[dict[str, Any]] = []

        if isinstance(content, str):
            if content.strip():
                parts.append({"text": content})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")

                if block_type == "text":
                    text = block.get("text", "")
                    if text.strip():
                        parts.append({"text": text})

                elif block_type == "tool_use":
                    parts.append(
                        {
                            "functionCall": {
                                "name": block.get("name", ""),
                                "args": block.get("input", {}),
                                "id": block.get("id", str(uuid.uuid4())),
                            }
                        }
                    )

                elif block_type == "tool_result":
                    is_error = block.get("is_error", False)
                    content_text = block.get("content", "")
                    if isinstance(content_text, list):
                        # Flatten list content to string
                        content_text = " ".join(
                            b.get("text", "") for b in content_text if isinstance(b, dict)
                        )
                    response_payload = (
                        {"error": content_text} if is_error else {"content": content_text}
                    )
                    parts.append(
                        {
                            "functionResponse": {
                                "name": "tool",  # Will be resolved from context
                                "response": response_payload,
                                "id": block.get("tool_use_id", ""),
                            }
                        }
                    )

                elif block_type == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        parts.append(
                            {
                                "inlineData": {
                                    "mimeType": source.get("media_type", "image/png"),
                                    "data": source.get("data", ""),
                                }
                            }
                        )

        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    return contents


def _resolve_tool_names(contents: list[dict[str, Any]]) -> None:
    """Resolve tool names for functionResponse parts by scanning history."""
    # Build a map of tool_use_id -> tool_name from assistant messages
    tool_names: dict[str, str] = {}
    for content in contents:
        if content.get("role") != "model":
            continue
        for part in content.get("parts", []):
            fc = part.get("functionCall")
            if fc:
                tool_names[fc.get("id", "")] = fc.get("name", "tool")

    # Now fill in functionResponse names
    for content in contents:
        if content.get("role") != "user":
            continue
        for part in content.get("parts", []):
            fr = part.get("functionResponse")
            if fr and not fr.get("name", "").strip():
                tool_id = fr.get("id", "")
                fr["name"] = tool_names.get(tool_id, "tool")


def build_generate_content_request(
    messages: list[dict[str, Any]],
    system: str,
    tools: list[dict[str, Any]] | None = None,
    model: str = "gemini-3-flash",
    project: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Build a complete Gemini generateContent request body.

    This converts the Anthropic Messages protocol (as received from Claude Code)
    into the Gemini-native format required by the Antigravity Cloud Code backend.
    """
    has_tools = bool(tools)
    contents = _convert_messages(messages)
    _resolve_tool_names(contents)

    request: dict[str, Any] = {
        "model": model,
        "project": project,
        "userPromptId": str(uuid.uuid4()),
        "request": {
            "contents": contents,
            "systemInstruction": _build_system_instruction(system, has_tools),
            "tools": _build_tools(tools),
        },
    }

    if has_tools:
        request["request"]["toolConfig"] = {
            "functionCallingConfig": {"mode": "AUTO"},
        }

    if session_id:
        request["request"]["session_id"] = session_id

    return request


def build_force_function_call_request(
    messages: list[dict[str, Any]],
    system: str,
    tools: list[dict[str, Any]] | None = None,
    model: str = "gemini-3-flash",
    project: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Build a request that forces function calling mode ANY (for retry)."""
    request = build_generate_content_request(
        messages, system, tools, model, project, session_id
    )
    if tools:
        request["request"]["toolConfig"] = {
            "functionCallingConfig": {"mode": "ANY"},
        }
    return request
