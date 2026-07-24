"""Google Cloud Code Antigravity provider (Gemini generateContent API)."""

from __future__ import annotations

from .client import AntigravityProvider
from .schema import antigravity_compatible_schema

__all__ = ("AntigravityProvider", "antigravity_compatible_schema")
