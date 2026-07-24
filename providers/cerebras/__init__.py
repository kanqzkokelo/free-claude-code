from __future__ import annotations
"""Cerebras Inference (OpenAI-compatible) adapter."""

from providers.defaults import CEREBRAS_DEFAULT_BASE

from .client import CerebrasProvider

__all__ = ["CEREBRAS_DEFAULT_BASE", "CerebrasProvider"]
