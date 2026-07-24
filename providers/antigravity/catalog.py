"""Antigravity model catalog: fetch, parse, cache, and persist.

Ported from jcode's ``jcode-provider-antigravity`` and
``jcode-base/provider/antigravity`` crates (Rust).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from .auth import _antigravity_headers, load_or_refresh_tokens

# API endpoints
FETCH_MODELS_API_URL = (
    "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
)

CATALOG_REFRESH_TTL_HOURS = 6

DEFAULT_FALLBACK_MODEL = "gemini-3-flash"

AVAILABLE_MODELS: list[str] = [
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
    "gemini-3-flash",
    "gemini-3-flash-agent",
    "gemini-3.5-flash-low",
    "gemini-3.6-flash",
    "gpt-oss-120b-medium",
]

# Model IDs the backend advertises but cannot actually service.
_REMAP_TABLE: dict[str, str] = {
    "gemini-3.1-pro-high": "gemini-pro-agent",
}


@dataclass
class CatalogModel:
    id: str
    display_name: str | None = None
    reset_time: str | None = None
    tag_title: str | None = None
    model_provider: str | None = None
    max_tokens: int | None = None
    max_output_tokens: int | None = None
    recommended: bool = False
    available: bool = True
    remaining_fraction_milli: int | None = None


@dataclass
class CatalogSnapshot:
    models: list[CatalogModel] = field(default_factory=list)
    default_model_id: str | None = None


@dataclass
class PersistedCatalog:
    models: list[CatalogModel] = field(default_factory=list)
    fetched_at_rfc3339: str = ""
    default_model_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "models": [
                {k: v for k, v in m.__dict__.items() if v is not None and v is not False}
                for m in self.models
            ],
            "fetched_at_rfc3339": self.fetched_at_rfc3339,
            "default_model_id": self.default_model_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersistedCatalog:
        models = [
            CatalogModel(
                id=m["id"],
                display_name=m.get("display_name"),
                reset_time=m.get("reset_time"),
                tag_title=m.get("tag_title"),
                model_provider=m.get("model_provider"),
                max_tokens=m.get("max_tokens"),
                max_output_tokens=m.get("max_output_tokens"),
                recommended=m.get("recommended", False),
                available=m.get("available", True),
                remaining_fraction_milli=m.get("remaining_fraction_milli"),
            )
            for m in data.get("models", [])
        ]
        return cls(
            models=models,
            fetched_at_rfc3339=data.get("fetched_at_rfc3339", ""),
            default_model_id=data.get("default_model_id"),
        )


def _catalog_cache_path() -> Path:
    home = os.environ.get("FCC_HOME") or os.path.expanduser("~")
    return Path(home) / ".fcc" / "antigravity_models_cache.json"


def load_persisted_catalog() -> PersistedCatalog | None:
    """Load the persisted warm catalog, or None if missing/stale."""
    path = _catalog_cache_path()
    if not path.is_file():
        return None
    try:
        catalog = PersistedCatalog.from_dict(json.loads(path.read_text("utf-8")))
        if not catalog.models:
            return None
        return catalog
    except Exception as exc:
        logger.debug("Failed to load Antigravity catalog cache: {}", exc)
        return None


def persist_catalog(snapshot: CatalogSnapshot) -> None:
    """Persist the warm catalog to disk."""
    if not snapshot.models:
        return
    path = _catalog_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    catalog = PersistedCatalog(
        models=snapshot.models,
        fetched_at_rfc3339=_now_rfc3339(),
        default_model_id=snapshot.default_model_id,
    )
    try:
        path.write_text(json.dumps(catalog.to_dict(), indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist Antigravity catalog: {}", exc)


def catalog_is_stale(fetched_at_rfc3339: str) -> bool:
    """Whether the persisted catalog is older than the TTL."""
    try:
        from datetime import datetime, timezone

        fetched = datetime.fromisoformat(fetched_at_rfc3339.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
        return age_hours >= CATALOG_REFRESH_TTL_HOURS
    except Exception:
        return True


def _now_rfc3339() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _remaining_fraction_to_milli(value: float | None) -> int | None:
    if value is None or not (-1e9 < value < 1e9):
        return None
    clamped = max(0.0, min(1.0, value))
    return round(clamped * 1000)


def merge_antigravity_model_ids(models: list[str]) -> list[str]:
    """Merge model ids, putting known models first in canonical order."""
    trimmed = [m.strip() for m in models if m.strip()]
    seen: set[str] = set()
    preferred: list[str] = []
    for known in AVAILABLE_MODELS:
        if known in trimmed and known not in seen:
            preferred.append(known)
            seen.add(known)
    extras = sorted(m for m in trimmed if m not in seen)
    return preferred + extras


def parse_fetch_available_models_response(
    response: dict[str, Any],
) -> CatalogSnapshot:
    """Parse the backend fetchAvailableModels response into a CatalogSnapshot."""
    default_model_id = (response.get("defaultAgentModelId") or "").strip() or None

    preferred_ids: list[str] = []
    if default_model_id:
        preferred_ids.append(default_model_id)
    for mid in response.get("commandModelIds", []):
        if mid.strip():
            preferred_ids.append(mid.strip())
    for mid in response.get("models", {}):
        if mid.strip():
            preferred_ids.append(mid.strip())

    ordered_ids = merge_antigravity_model_ids(preferred_ids)
    models_raw = response.get("models", {})
    by_id: dict[str, CatalogModel] = {}

    for model_id, entry in models_raw.items():
        mid = model_id.strip()
        if not mid:
            continue
        quota = entry.get("quotaInfo") or {}
        remaining = quota.get("remainingFraction")
        available = (remaining is None) or (remaining > 0)
        model = CatalogModel(
            id=mid,
            display_name=_clean_str(entry.get("displayName")),
            reset_time=_clean_str(quota.get("resetTime")),
            tag_title=_clean_str(entry.get("tagTitle")),
            model_provider=_clean_str(entry.get("modelProvider")),
            max_tokens=entry.get("maxTokens"),
            max_output_tokens=entry.get("maxOutputTokens"),
            recommended=entry.get("recommended", False),
            available=available,
            remaining_fraction_milli=_remaining_fraction_to_milli(remaining),
        )
        by_id[mid] = model
        # Alias via modelName
        alias = _clean_str(entry.get("modelName"))
        if alias and alias != mid and alias not in by_id:
            by_id[alias] = model

    models = [
        by_id.get(
            mid,
            CatalogModel(id=mid, available=True),
        )
        for mid in ordered_ids
    ]
    # Sort: available first
    models.sort(key=lambda m: (not m.available, m.id))
    return CatalogSnapshot(models=models, default_model_id=default_model_id)


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v if v else None


def remap_unsupported_model(model: str) -> str:
    """Remap model ids the backend advertises but cannot service."""
    return _REMAP_TABLE.get(model, model)


def catalog_model_detail(model: CatalogModel) -> str:
    """Human-readable detail string for a catalog model."""
    parts: list[str] = []
    if model.display_name and model.display_name != model.id:
        parts.append(model.display_name)
    if model.recommended:
        parts.append("recommended")
    if model.tag_title:
        parts.append(model.tag_title)
    if model.model_provider:
        parts.append(model.model_provider.lower())
    if model.remaining_fraction_milli is not None:
        percent = model.remaining_fraction_milli / 10.0
        parts.append(f"quota {percent:.1f}%")
    if model.reset_time:
        parts.append(f"resets {model.reset_time}")
    return " · ".join(parts)


async def fetch_catalog_snapshot() -> CatalogSnapshot:
    """Fetch the live Antigravity model catalog using the resolved credential."""
    tokens = load_or_refresh_tokens()
    headers = _antigravity_headers(tokens.access_token)
    body: dict[str, Any] = {}
    if tokens.project_id:
        body["project"] = tokens.project_id

    resp = httpx.post(
        FETCH_MODELS_API_URL,
        headers=headers,
        json=body,
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Antigravity model catalog request failed (HTTP {resp.status_code}): {resp.text}"
        )
    data = resp.json()
    return parse_fetch_available_models_response(data)
