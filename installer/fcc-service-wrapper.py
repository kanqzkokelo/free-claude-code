"""
Free Claude Code - Windows Service Wrapper

This script is called by nssm (the Windows Service Manager) to run the
Free Claude Code proxy as a background Windows service.

It sets up the Python environment and starts the uvicorn server.
"""
from __future__ import annotations

import os
import sys

# ── Determine paths ──────────────────────────────────────────────────────────
SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SERVICE_DIR, "src")
VENV_SITE = os.path.join(SERVICE_DIR, "venv", "Lib", "site-packages")

# Add the source dir and venv site-packages to the Python path
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

# Set working directory so relative path lookups work
os.chdir(SRC_DIR)

# ── Environment ──────────────────────────────────────────────────────────────
# Don't open the browser when running as a service
os.environ.setdefault("FCC_OPEN_BROWSER", "false")
# Load the managed .env if it exists (it should be at {app}\.env)
managed_env = os.path.join(SERVICE_DIR, ".env")
if os.path.isfile(managed_env):
    with open(managed_env, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and not os.environ.get(key):
                os.environ[key] = value

# ── Run the server ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    from cli.entrypoints import serve

    sys.exit(serve())
