"""Antigravity OAuth login CLI.

Run ``fcc-antigravity-login`` to authenticate with your Google account
for the Cloud Code Antigravity backend. This performs a PKCE OAuth flow
and persists tokens to ``~/.fcc/antigravity_oauth.json``.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode, urlparse

from .auth import (
    ANTIGRAVITY_CLIENT_ID,
    ANTIGRAVITY_CLIENT_SECRET,
    ANTIGRAVITY_SCOPES,
    GOOGLE_AUTHORIZE_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    GOOGLE_OAUTH_USER_AGENT,
    _client_id,
    _client_secret,
    save_tokens,
    AntigravityTokens,
    _fetch_email,
    _fetch_project_id,
)

REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 51121
REDIRECT_PATH = "/oauth-callback"
DEFAULT_REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}{REDIRECT_PATH}"


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code verifier and S256 challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64

    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _build_auth_url(redirect_uri: str, challenge: str, state: str) -> str:
    scope = " ".join(ANTIGRAVITY_SCOPES)
    client_id = _client_id()
    return (
        f"{GOOGLE_AUTHORIZE_URL}?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
        f"&state={state}"
        f"&access_type=offline"
        f"&prompt=consent"
    )


def _exchange_code(
    code: str, verifier: str, redirect_uri: str
) -> AntigravityTokens:
    """Exchange an authorization code for tokens."""
    import httpx

    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        headers={"User-Agent": GOOGLE_OAUTH_USER_AGENT},
        data={
            "grant_type": "authorization_code",
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "code": code.strip(),
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Antigravity token exchange failed (HTTP {resp.status_code}): {resp.text}"
        )
    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "No refresh token received. Revoke access at "
            "https://myaccount.google.com/permissions and try again."
        )
    expires_in = data.get("expires_in", 3600)
    access_token = data["access_token"]
    email = _fetch_email(access_token)
    project_id = _fetch_project_id(access_token)
    return AntigravityTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at_ms=int(time.time() * 1000) + expires_in * 1000,
        email=email,
        project_id=project_id,
    )


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler to receive the OAuth callback."""

    code: str | None = None
    state: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_error(404)
            return
        params = parse_qs(parsed.query)
        _OAuthCallbackHandler.code = params.get("code", [None])[0]
        _OAuthCallbackHandler.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Antigravity login successful!</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, format, *args):
        pass  # Suppress request logging


def _wait_for_callback(timeout: float = 300) -> tuple[str, str]:
    """Start a local HTTP server and wait for the OAuth callback."""
    server = HTTPServer((REDIRECT_HOST, REDIRECT_PORT), _OAuthCallbackHandler)
    server.timeout = timeout
    print(f"Waiting for OAuth callback on {DEFAULT_REDIRECT_URI}...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        server.handle_request()
        if _OAuthCallbackHandler.code:
            code = _OAuthCallbackHandler.code
            state = _OAuthCallbackHandler.state or ""
            _OAuthCallbackHandler.code = None
            _OAuthCallbackHandler.state = None
            return code, state
    raise TimeoutError(f"Timed out waiting for OAuth callback ({timeout}s)")


def main() -> None:
    """Run the Antigravity OAuth login flow."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    auth_url = _build_auth_url(DEFAULT_REDIRECT_URI, challenge, state)

    print("\n=== Antigravity Login ===\n")
    print("Opening browser for Google OAuth login...\n")
    print(f"If the browser didn't open, visit:\n{auth_url}\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        print("Could not open browser automatically.\n")

    try:
        code, callback_state = _wait_for_callback()
    except TimeoutError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        print("Please try again.", file=sys.stderr)
        raise SystemExit(1) from exc

    if callback_state != state:
        print("\nError: OAuth state mismatch. Please try again.", file=sys.stderr)
        raise SystemExit(1)

    print("Exchanging authorization code for tokens...")
    try:
        tokens = _exchange_code(code, verifier, DEFAULT_REDIRECT_URI)
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    save_tokens(tokens)
    print("\nLogin successful!")
    if tokens.email:
        print(f"  Account: {tokens.email}")
    if tokens.project_id:
        print(f"  Project: {tokens.project_id}")
    print(f"\nTokens saved to: ~/.fcc/antigravity_oauth.json")
    print("\nYou can now use Antigravity as a provider:")
    print('  MODEL="antigravity/gemini-3-flash"')


if __name__ == "__main__":
    main()
