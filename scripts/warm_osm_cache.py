"""Fetch every state's OpenStreetMap geometry into the local cache.

Why: OSM is by far the most expensive source. Measured on Telangana's 33
district centres, of the ~43 s a cold state costs, river context alone is 36 s
and everything else together is 7 s. On a serverless host with a 60 s ceiling
that is the difference between a page and a gateway timeout.

OSM geometry barely changes, so it belongs in the bundled seed rather than in a
per-request fetch. This fills the local cache; ``scripts/export_seed.py`` then
captures it into data/seed/cache_seed.json.gz, which every cold start loads.

    python scripts/warm_osm_cache.py [--states a,b,c] [--skip-infrastructure]

Deliberately sequential: Overpass is a shared community service that rate-limits
per IP, and running this in parallel is how you get 429s and a partial snapshot.
Re-runnable - anything already cached is skipped by the cache itself.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database.db import init_db  # noqa: E402


async def warm(state_ids: list[str] | None, infrastructure: bool) -> int:
    from app.services import india_service, osm_service, region_service

    init_db()
    region_service.reload_regions()

    states = india_service.list_states()
    if state_ids:
        wanted = {s.strip() for s in state_ids}
        states = [s for s in states if s["id"] in wanted]
    print(f"warming OSM geometry for {len(states)} states (sequentially, by design)")

    failures = 0
    started = time.perf_counter()
    for index, state in enumerate(states, 1):
        state_id = state["id"]
        label = f"[{index:2}/{len(states)}] {state_id:<34}"
        began = time.perf_counter()
        try:
            region = region_service.get_region(state_id)
            locations = region_service.get_locations(region["id"])
            points = [(l.id, l.latitude, l.longitude) for l in locations]
            await osm_service.get_river_context_batch(points)
            note = f"{len(points)} points"
            if infrastructure:
                infra = await osm_service.get_region_infrastructure(region)
                note += f", {len(infra.get('features', []))} features"
        except Exception as exc:  # noqa: BLE001 - one bad state must not stop the rest
            failures += 1
            note = f"FAILED {type(exc).__name__}: {exc}"[:80]
        print(f"{label} {time.perf_counter() - began:6.1f}s  {note}", flush=True)

    print(f"done in {time.perf_counter() - started:.0f}s, {failures} state(s) failed")
    if failures:
        print("Re-run to retry the failures; cached states are skipped automatically.")
    return 1 if failures == len(states) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", help="comma-separated state ids; default is all")
    ap.add_argument("--skip-infrastructure", action="store_true",
                    help="river context only, skipping the heavier facilities query")
    args = ap.parse_args()
    return asyncio.run(warm(
        args.states.split(",") if args.states else None,
        not args.skip_infrastructure,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
