"""Google OAuth token management for Antigravity / Cloud Code backend.

Ported from jcode's ``jcode-provider-antigravity-runtime`` and
``jcode-base/auth/antigravity`` (Rust). Uses the same OAuth client
credentials embedded in Google's Antigravity desktop app.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

# OAuth endpoints
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"

# Cloud Code endpoints for project resolution
LOAD_ENDPOINTS = (
    "https://cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://autopush-cloudcode-pa.sandbox.googleapis.com",
)

# Public OAuth client credentials (from Google's Antigravity desktop app).
# These are safe to embed -- they identify the public desktop OAuth client.
ANTIGRAVITY_CLIENT_ID = (
    "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
)
ANTIGRAVITY_CLIENT_SECRET = "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"

ANTIGRAVITY_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

GOOGLE_OAUTH_USER_AGENT = "google-api-nodejs-client/9.15.1"


def _token_path() -> Path:
    """Return the path to the persisted Antigravity OAuth tokens."""
    home = os.environ.get("FCC_HOME") or os.path.expanduser("~")
    return Path(home) / ".fcc" / "antigravity_oauth.json"


def _client_id() -> str:
    env = os.environ.get("ANTIGRAVITY_CLIENT_ID", "").strip()
    return env or ANTIGRAVITY_CLIENT_ID


def _client_secret() -> str:
    env = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "").strip()
    return env or ANTIGRAVITY_CLIENT_SECRET


class AntigravityTokens:
    """Persisted Google OAuth tokens for the Antigravity backend."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        expires_at_ms: int,
        email: str | None = None,
        project_id: str | None = None,
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at_ms
        self.email = email
        self.project_id = project_id

    def is_expired(self) -> bool:
        return self.expires_at <= int(time.time() * 1000) + 60_000

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at_ms": self.expires_at,
        }
        if self.email:
            d["email"] = self.email
        if self.project_id:
            d["project_id"] = self.project_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AntigravityTokens:
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at_ms=data.get("expires_at_ms") or data.get("expires_at", 0),
            email=data.get("email"),
            project_id=data.get("project_id"),
        )


def load_tokens() -> AntigravityTokens | None:
    """Load persisted Antigravity tokens, or None if missing."""
    path = _token_path()
    if not path.is_file():
        return None
    try:
        return AntigravityTokens.from_dict(json.loads(path.read_text("utf-8")))
    except Exception as exc:
        logger.warning("Failed to load Antigravity tokens: {}", exc)
        return None


def save_tokens(tokens: AntigravityTokens) -> None:
    """Persist Antigravity tokens to disk."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens.to_dict(), indent=2), encoding="utf-8")


def load_or_refresh_tokens() -> AntigravityTokens:
    """Load tokens and refresh if expired. Raises RuntimeError if unavailable."""
    tokens = load_tokens()
    if tokens is None:
        raise RuntimeError(
            "No Antigravity tokens found. Run `fcc-antigravity-login` to authenticate."
        )
    if tokens.is_expired():
        tokens = _refresh_tokens(tokens)
    return tokens


def _refresh_tokens(tokens: AntigravityTokens) -> AntigravityTokens:
    """Refresh an expired access token using the refresh token."""
    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        headers={"User-Agent": GOOGLE_OAUTH_USER_AGENT},
        data={
            "grant_type": "refresh_token",
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "refresh_token": tokens.refresh_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Antigravity token refresh failed (HTTP {resp.status_code}): {resp.text}"
        )
    data = resp.json()
    new_refresh = data.get("refresh_token", tokens.refresh_token)
    expires_in = data.get("expires_in", 3600)
    refreshed = AntigravityTokens(
        access_token=data["access_token"],
        refresh_token=new_refresh,
        expires_at_ms=int(time.time() * 1000) + expires_in * 1000,
        email=tokens.email,
        project_id=tokens.project_id,
    )
    # Fill in email/project_id if missing
    if not refreshed.email:
        refreshed.email = _fetch_email(refreshed.access_token)
    if not refreshed.project_id:
        refreshed.project_id = _fetch_project_id(refreshed.access_token)
    save_tokens(refreshed)
    return refreshed


def _fetch_email(access_token: str) -> str | None:
    """Fetch the Google account email from the userinfo endpoint."""
    try:
        resp = httpx.get(
            GOOGLE_USERINFO_URL,
            headers={
                "User-Agent": GOOGLE_OAUTH_USER_AGENT,
                "Authorization": f"Bearer {access_token}",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            email = resp.json().get("email", "")
            return email if email.strip() else None
    except Exception as exc:
        logger.debug("Failed to fetch Antigravity email: {}", exc)
    return None


def _fetch_project_id(access_token: str) -> str | None:
    """Resolve the Cloud Code project ID via loadCodeAssist."""
    headers = _antigravity_headers(access_token)
    body = {
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }
    }
    for base_url in LOAD_ENDPOINTS:
        try:
            resp = httpx.post(
                f"{base_url}/v1internal:loadCodeAssist",
                headers=headers,
                json=body,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                project_ref = data.get("cloudaicompanionProject")
                if isinstance(project_ref, str) and project_ref.strip():
                    return project_ref.strip()
                if isinstance(project_ref, dict):
                    pid = project_ref.get("id", "")
                    if isinstance(pid, str) and pid.strip():
                        return pid.strip()
        except Exception as exc:
            logger.debug("loadCodeAssist failed for {}: {}", base_url, exc)
    return None


def _antigravity_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": _user_agent(),
        "x-goog-api-client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "client-metadata": _client_metadata_header(),
    }


def _user_agent() -> str:
    version = os.environ.get("FCC_ANTIGRAVITY_VERSION", "1.18.3").strip()
    import platform

    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        return f"antigravity/{version} windows/amd64"
    if machine in ("arm64", "aarch64"):
        return f"antigravity/{version} darwin/arm64"
    return f"antigravity/{version} darwin/amd64"


def _client_metadata_header() -> str:
    return (
        '{"ideType":"ANTIGRAVITY","platform":"PLATFORM_UNSPECIFIED","pluginType":"GEMINI"}'
    )
