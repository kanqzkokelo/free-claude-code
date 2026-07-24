from __future__ import annotations
"""
Claude Code Proxy - Entry Point

Minimal entry point that builds the ASGI app via :func:`api.app.create_app`.
Run with: uv run uvicorn server:app --host 0.0.0.0 --port 8082 --timeout-graceful-shutdown 5
"""

import sys

class DummyStream:
    def write(self, data):
        return len(data)
    def flush(self):
        pass
    def isatty(self):
        return False
    def fileno(self):
        raise OSError("No file descriptor")
    def writable(self):
        return True

if sys.stdout is None:
    sys.stdout = DummyStream()
if sys.stderr is None:
    sys.stderr = DummyStream()

if not hasattr(sys, "argv") or sys.argv is None:
    sys.argv = [""]
elif not sys.argv or sys.argv[0] is None:
    sys.argv = [""] + sys.argv[1:]

from api.app import create_app, create_asgi_app

app = create_asgi_app()

__all__ = ["app", "create_app"]

if __name__ == "__main__":
    import uvicorn

    from cli.process_registry import kill_all_best_effort
    from config.settings import get_settings

    settings = get_settings()
    try:
        # timeout_graceful_shutdown ensures uvicorn doesn't hang on task cleanup.
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level="debug",
            timeout_graceful_shutdown=5,
        )
    finally:
        # Safety net: cleanup subprocesses if lifespan shutdown doesn't fully run.
        kill_all_best_effort()
