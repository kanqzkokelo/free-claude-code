"""JSON Schema normalization for the Antigravity Cloud Code backend.

The Cloud Code backend multiplexes multiple upstreams (Gemini-native,
Gemini-to-Anthropic translation, OpenAI-compatible bridges), each with
different JSON Schema requirements. This module rewrites schemas per-model
so tool calls are accepted by every upstream.

Ported from jcode's ``jcode-provider-antigravity`` crate (Rust).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


# Numeric JSON Schema bounds the OpenAI-compatible bridge corrupts when
# round-tripping through a protobuf int64.
NUMERIC_SCHEMA_BOUND_KEYS = frozenset(
    {
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minProperties",
        "maxProperties",
    }
)


def model_is_gemini(model: str) -> bool:
    """Whether the resolved model targets the Gemini native path."""
    return model.strip().lower().startswith("gemini")


def model_is_claude(model: str) -> bool:
    """Whether the resolved model targets an Anthropic Claude model."""
    return "claude" in model.strip().lower()


def strip_numeric_schema_bounds(schema: Any) -> Any:
    """Recursively drop numeric bounds that the OpenAI bridge mangles."""
    if isinstance(schema, dict):
        return {
            k: strip_numeric_schema_bounds(v)
            for k, v in schema.items()
            if k not in NUMERIC_SCHEMA_BOUND_KEYS
        }
    if isinstance(schema, list):
        return [strip_numeric_schema_bounds(item) for item in schema]
    return schema


def flatten_schema_combiners(schema: Any) -> Any:
    """Collapse anyOf/oneOf/allOf to their first branch.

    The Claude backend (Gemini->Anthropic translation) rejects combiners
    with HTTP 400. Collapsing to the first branch preserves a usable schema.
    """
    if not isinstance(schema, dict):
        if isinstance(schema, list):
            return [flatten_schema_combiners(item) for item in schema]
        return schema

    for combiner in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(combiner)
        if isinstance(branches, list) and branches:
            first = flatten_schema_combiners(branches[0])
            if isinstance(first, dict):
                merged = dict(first)
                for key, value in schema.items():
                    if key != combiner:
                        merged.setdefault(key, flatten_schema_combiners(value))
                return merged
            return first

    return {k: flatten_schema_combiners(v) for k, v in schema.items()}


_GEMINI_UNSUPPORTED_PARAMS = frozenset(
    {
        "propertyNames",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "default",
        "examples",
        "if",
        "then",
        "else",
        "not",
        "$comment",
    }
)


def strip_gemini_unsupported_params(schema: Any) -> Any:
    """Strip JSON Schema keywords the Gemini generateContent API rejects."""
    if isinstance(schema, dict):
        return {
            k: strip_gemini_unsupported_params(v)
            for k, v in schema.items()
            if k not in _GEMINI_UNSUPPORTED_PARAMS
        }
    if isinstance(schema, list):
        return [strip_gemini_unsupported_params(item) for item in schema]
    return schema


def antigravity_compatible_schema(schema: Any, model: str) -> Any:
    """Normalize a tool-parameter JSON schema for the resolved model.

    All models: strip unsupported JSON Schema keywords first.
    - Gemini models: strip unsupported keywords only.
    - Claude models: flatten combiners (Anthropic rejects them).
    - Other models (gpt-oss, etc.): flatten combiners + strip numeric bounds.
    """
    cleaned = strip_gemini_unsupported_params(deepcopy(schema))
    if model_is_gemini(model):
        return cleaned
    if model_is_claude(model):
        return flatten_schema_combiners(cleaned)
    return strip_numeric_schema_bounds(flatten_schema_combiners(cleaned))
