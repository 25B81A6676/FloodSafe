"""Capture the slow, slow-changing cache entries into a bundled seed file.

Run this against a warmed local backend (after `prefetch complete` appears in
the log) whenever the OpenStreetMap snapshot should be refreshed:

    python scripts/export_seed.py

Only OSM geometry and derived terrain are captured. Weather, river discharge and
rainfall climatology are time-sensitive and are deliberately excluded — see
backend/app/services/seed_cache.py.
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.seed_cache import SEEDABLE_SOURCES  # noqa: E402

DB = ROOT / "data" / "cache" / "floodsafe.db"
OUT = ROOT / "data" / "seed" / "cache_seed.json.gz"


def main() -> int:
    if not DB.exists():
        print(f"No cache database at {DB}. Start the backend and let it warm first.")
        return 1

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(SEEDABLE_SOURCES))
    rows = conn.execute(
        f"SELECT cache_key, source, payload, fetched_at FROM api_cache "
        f"WHERE source IN ({placeholders})",
        SEEDABLE_SOURCES,
    ).fetchall()

    if not rows:
        print("Nothing to export — the cache holds no seedable entries yet.")
        return 1

    entries = [
        {
            "cache_key": r["cache_key"],
            "source": r["source"],
            "fetched_at": r["fetched_at"],
            "payload": json.loads(r["payload"]),
        }
        for r in rows
    ]

    seed = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Snapshot of OpenStreetMap geometry and derived terrain, so a "
            "serverless cold start does not re-query Overpass. Real data, "
            "loaded with its original fetch timestamp."
        ),
        "sources": sorted({e["source"] for e in entries}),
        "entries": entries,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(seed, fh, separators=(",", ":"))

    by_source: dict[str, int] = {}
    for e in entries:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1

    print(f"Wrote {OUT.relative_to(ROOT)}")
    print(f"  entries : {len(entries)}  ({', '.join(f'{k} x{v}' for k, v in sorted(by_source.items()))})")
    print(f"  size    : {OUT.stat().st_size / 1024 / 1024:.2f} MB gzipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
