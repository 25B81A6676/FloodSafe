"""Bundled seed cache for slow, slow-changing sources.

Why this exists
---------------
OpenStreetMap geometry is the most expensive thing the platform fetches: a
region-wide Overpass query takes 12-16 seconds and Overpass rate-limits shared
IP addresses. Terrain is cheap to store but costs several DEM round trips and
never changes.

On a long-lived server the startup prefetch warms both once and the SQLite cache
carries them for a week. On a serverless host there is no long-lived process and
no persistent disk, so every cold start would pay that cost again — which both
times out the request and hammers a shared community service.

So the repository ships a snapshot of exactly those entries, captured by
``scripts/export_seed.py``, and this module loads it into the ordinary cache at
startup. Nothing else changes: the services still read through
``data_cache``, and the entries keep their **original fetch timestamp**, so the
UI reports their real age rather than pretending they are fresh.

Time-sensitive sources — weather, river discharge, rainfall climatology — are
deliberately NOT seeded. Those must always be fetched live.
"""
from __future__ import annotations

import gzip
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.db import get_conn, jdump, write_conn
from app.services.data_cache import iso, parse_iso, utcnow

log = get_logger(__name__)

SEED_PATH = settings.data_dir / "seed" / "cache_seed.json.gz"

#: Only these sources are seedable. Everything else is time-sensitive and must
#: be fetched live, so a stale snapshot would be worse than a slow request.
SEEDABLE_SOURCES = ("overpass", "terrain", "osm-river")

#: How long a seeded entry stays valid once loaded. Matches the normal OSM TTL.
SEED_TTL = timedelta(days=7)


def load_seed() -> dict[str, Any]:
    """Insert bundled entries for any cache key that is not already present.

    Existing entries always win — a live fetch is better than a snapshot, so
    this never overwrites anything the running instance has already retrieved.
    """
    if not SEED_PATH.exists():
        log.info("no bundled cache seed at %s (fine — everything will be fetched live)", SEED_PATH)
        return {"loaded": 0, "skipped": 0, "available": False}

    try:
        with gzip.open(SEED_PATH, "rt", encoding="utf-8") as fh:
            seed = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("cache seed unreadable (%s) — continuing without it", exc)
        return {"loaded": 0, "skipped": 0, "available": False}

    entries = seed.get("entries") or []
    with get_conn() as conn:
        existing = {r["cache_key"] for r in conn.execute("SELECT cache_key FROM api_cache")}

    now = utcnow()
    loaded = skipped = 0

    with write_conn() as conn:
        for e in entries:
            key = e.get("cache_key")
            if not key or e.get("source") not in SEEDABLE_SOURCES:
                skipped += 1
                continue
            if key in existing:
                skipped += 1
                continue
            try:
                fetched = parse_iso(e["fetched_at"])
            except (KeyError, ValueError):
                fetched = now
            conn.execute(
                """INSERT INTO api_cache (cache_key, source, payload, fetched_at, expires_at, hit_count)
                   VALUES (?,?,?,?,?,0)""",
                (key, e["source"], jdump(e["payload"]), iso(fetched), iso(now + SEED_TTL)),
            )
            loaded += 1

    log.info(
        "cache seed: loaded %d entr%s, skipped %d (captured %s)",
        loaded, "y" if loaded == 1 else "ies", skipped, seed.get("captured_at", "unknown"),
    )
    return {
        "loaded": loaded,
        "skipped": skipped,
        "available": True,
        "captured_at": seed.get("captured_at"),
    }
