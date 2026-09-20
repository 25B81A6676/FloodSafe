"""Capture the slow, slow-changing cache entries into a bundled seed file.

Run this against a warmed local backend (after `prefetch complete` appears in
the log) whenever the OpenStreetMap snapshot should be refreshed:

    python scripts/export_seed.py

Only OSM geometry and derived terrain are captured. Weather, river discharge and
rainfall climatology are time-sensitive and are deliberately excluded — see
backend/app/services/seed_cache.py.
"""
from __future__ import annotations

import argparse
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

# Oversized entries are skipped rather than bundled. A single state-wide
# Overpass facilities response can be 39 MB; a handful of those would dwarf the
# repository, slow every cold start that has to load them, and buy nothing -
# the entries that actually cost the request are the small per-location river
# lookups. Skipped entries are simply fetched live, as they were before.
MAX_ENTRY_BYTES = 1_500_000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-entry-bytes", type=int, default=MAX_ENTRY_BYTES,
                    help="skip cache entries larger than this (0 disables the cap)")
    args = ap.parse_args()

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

    entries = []
    skipped_bytes = 0
    skipped: list[tuple[str, int]] = []
    for r in rows:
        size = len(r["payload"])
        if args.max_entry_bytes and size > args.max_entry_bytes:
            skipped.append((r["cache_key"], size))
            skipped_bytes += size
            continue
        entries.append({
            "cache_key": r["cache_key"],
            "source": r["source"],
            "fetched_at": r["fetched_at"],
            "payload": json.loads(r["payload"]),
        })

    if not entries:
        print("Every seedable entry was over the size cap; nothing written.")
        return 1

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
    if skipped:
        print(f"  skipped : {len(skipped)} entr{'y' if len(skipped) == 1 else 'ies'} over "
              f"{args.max_entry_bytes / 1e6:.1f} MB ({skipped_bytes / 1e6:.0f} MB raw), "
              f"fetched live instead")
        for key, size in sorted(skipped, key=lambda kv: -kv[1])[:5]:
            print(f"            {size / 1e6:6.1f} MB  {key[:72]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
