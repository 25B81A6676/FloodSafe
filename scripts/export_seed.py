"""Capture the slow, slow-changing cache entries into a bundled seed.

Run this against a warmed local cache (see ``scripts/warm_osm_cache.py``)
whenever the OpenStreetMap snapshot should be refreshed:

    python scripts/export_seed.py

Only OSM geometry and derived terrain are captured. Weather, river discharge and
rainfall climatology are time-sensitive and are deliberately excluded - see
backend/app/services/seed_cache.py.

The snapshot is written **per region**, not as one file. A single file covering
the country took 6.8 s to load into SQLite, and a serverless instance pays that
on every cold start while the visitor waits - for 35 states they are not looking
at. Each state now loads only its own shard, and anything not attributable to
one state goes in a small shared shard that always loads.
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

from app.services.seed_cache import (  # noqa: E402
    SEEDABLE_SOURCES, SEED_DIR, seedable, shard_name,
)

DB = ROOT / "data" / "cache" / "floodsafe.db"

# Oversized entries are skipped rather than bundled. A single state-wide
# Overpass response can be 39 MB; a handful of those would dwarf the
# repository, slow every cold start that has to load them, and buy nothing -
# the entries that actually cost the request are the small per-location
# lookups. Skipped entries are simply fetched live, as they were before.
MAX_ENTRY_BYTES = 1_500_000


def coordinate_index() -> dict[tuple[str, str], str]:
    """Map rounded ``lat,lon`` to the region it belongs to.

    Most cache keys are coordinates, not region names, so attribution has to be
    rebuilt from the geography: every district centre and every risk-grid cell
    of every state. Keys are formatted exactly as ``data_cache.make_key`` does,
    so a lookup is a string comparison rather than a distance search.
    """
    from app.services import india_service, region_service

    region_service.reload_regions()
    index: dict[tuple[str, str], str] = {}
    for state in india_service.list_states():
        state_id = state["id"]
        try:
            region = region_service.get_region(state_id)
            points = [
                (loc.latitude, loc.longitude)
                for loc in region_service.get_locations(region["id"])
            ]
            points += [
                (cell.center_lat, cell.center_lon)
                for cell in region_service.get_grid_cells(region["id"])
            ]
        except Exception as exc:  # noqa: BLE001 - one bad state must not stop the export
            print(f"  ! could not index {state_id}: {type(exc).__name__}: {exc}")
            continue
        for lat, lon in points:
            index.setdefault((f"{lat:.4f}", f"{lon:.4f}"), state_id)
    return index


def region_of(cache_key: str, index: dict[tuple[str, str], str]) -> str | None:
    """Which region an entry belongs to, or None for the shared shard."""
    parts = dict(
        piece.split("=", 1) for piece in cache_key.split("|")[1:] if "=" in piece
    )
    if "region" in parts:
        # Keys such as "...|region=kerala__wayanad" belong to the parent state.
        return parts["region"].split("__", 1)[0]
    if "lat" in parts and "lon" in parts:
        return index.get((parts["lat"], parts["lon"]))
    return None


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
        print("Nothing to export - the cache holds no seedable entries yet.")
        return 1

    print("indexing geography...")
    index = coordinate_index()

    shards: dict[str, list[dict]] = {}
    skipped: list[tuple[str, int]] = []
    skipped_bytes = 0
    for r in rows:
        if not seedable(r["cache_key"], r["source"]):
            continue
        size = len(r["payload"])
        if args.max_entry_bytes and size > args.max_entry_bytes:
            skipped.append((r["cache_key"], size))
            skipped_bytes += size
            continue
        shard = region_of(r["cache_key"], index) or "common"
        shards.setdefault(shard, []).append({
            "cache_key": r["cache_key"],
            "source": r["source"],
            "fetched_at": r["fetched_at"],
            "payload": json.loads(r["payload"]),
        })

    if not shards:
        print("Every seedable entry was over the size cap; nothing written.")
        return 1

    captured_at = datetime.now(timezone.utc).isoformat()
    note = (
        "Snapshot of OpenStreetMap geometry and derived terrain, so a "
        "serverless cold start does not re-query Overpass. Real data, "
        "loaded with its original fetch timestamp."
    )

    SEED_DIR.mkdir(parents=True, exist_ok=True)
    for stale in SEED_DIR.glob("*.json.gz"):
        stale.unlink()

    total_bytes = 0
    for shard, entries in sorted(shards.items()):
        out = SEED_DIR / shard_name(shard)
        with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as fh:
            json.dump({
                "captured_at": captured_at,
                "note": note,
                "region": shard,
                "sources": sorted({e["source"] for e in entries}),
                "entries": entries,
            }, fh, separators=(",", ":"))
        total_bytes += out.stat().st_size

    entry_count = sum(len(v) for v in shards.values())
    by_source: dict[str, int] = {}
    for entries in shards.values():
        for e in entries:
            by_source[e["source"]] = by_source.get(e["source"], 0) + 1

    print(f"Wrote {len(shards)} shards to {SEED_DIR.relative_to(ROOT)}")
    print(f"  entries : {entry_count}  "
          f"({', '.join(f'{k} x{v}' for k, v in sorted(by_source.items()))})")
    print(f"  size    : {total_bytes / 1024 / 1024:.2f} MB gzipped total, "
          f"largest shard {max((SEED_DIR / shard_name(s)).stat().st_size for s in shards) / 1e6:.2f} MB")
    print(f"  shared  : {len(shards.get('common', []))} entries load on every start")
    if skipped:
        print(f"  skipped : {len(skipped)} entries over "
              f"{args.max_entry_bytes / 1e6:.1f} MB ({skipped_bytes / 1e6:.0f} MB raw), "
              f"fetched live instead")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
