"""Vercel serverless entry point.

Vercel builds from the repository root, so this module puts ``backend/`` on the
import path and re-exports the FastAPI application. Vercel's Python runtime
detects the ASGI ``app`` object and serves it directly.

Two things differ from running locally:

* **The filesystem is ephemeral and read-only apart from /tmp**, so the SQLite
  cache lives in /tmp. It survives between warm invocations on the same
  instance and is rebuilt on a cold one — which is exactly why the OpenStreetMap
  snapshot is bundled (see backend/app/services/seed_cache.py).
* **There is no long-lived process**, so the background prefetch is switched
  off. Each source is fetched on demand instead, and the seed covers the slow
  ones.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Must be set before app.config.settings is imported, since Settings is cached.
os.environ.setdefault("DATABASE_PATH", "/tmp/floodsafe.db")
# Settings creates the cache directory on import, and /var/task is read-only.
os.environ.setdefault("CACHE_DIR", "/tmp/floodsafe-cache")
os.environ.setdefault("STARTUP_PREFETCH", "false")
os.environ.setdefault("ENVIRONMENT", "production")

from app.main import app  # noqa: E402

__all__ = ["app"]


# A Vercel rewrite replaces the request path with the destination, so every
# route arrives as "/api/index" and FastAPI answers 404. There is no header
# carrying the original path, so vercel.json passes it in __vercel_path and
# this shim puts it back before the ASGI app sees the request.
_fastapi_app = app


async def app(scope, receive, send):  # type: ignore[no-redef]
    if scope["type"] == "http":
        from urllib.parse import parse_qsl, urlencode

        pairs = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
        original = next((v for k, v in pairs if k == "__vercel_path"), None)
        if original:
            scope = dict(scope)
            scope["path"] = original
            scope["raw_path"] = original.encode()
            scope["query_string"] = urlencode(
                [(k, v) for k, v in pairs if k != "__vercel_path"]
            ).encode()
    return await _fastapi_app(scope, receive, send)
