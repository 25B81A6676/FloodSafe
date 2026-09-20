"""Pre-warm the CDN cache for every state, so no visitor pays the cold cost.

The first request for a state fetches weather, terrain and river geometry for
every one of its districts and can take a minute. Once that response is in the
edge cache it is served in about a tenth of a second, and stale-while-revalidate
keeps it that way. This walks every state so the first visitor is this script.

    python scripts/warm_edge_cache.py [base_url] [--workers N]

Prints one line per state with the slowest endpoint, and a summary. Safe to
re-run: it only issues GETs.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import time

import httpx

ENDPOINTS = ("/api/dashboard/summary", "/api/risk/map", "/api/gis/layers")
DEFAULT_BASE = "https://floodsafe-rosy.vercel.app"


def warm_state(client: httpx.Client, base: str, state_id: str) -> tuple[str, float, str]:
    worst, worst_path = 0.0, ""
    for path in ENDPOINTS:
        started = time.perf_counter()
        try:
            r = client.get(f"{base}{path}", params={"region_id": state_id}, timeout=180.0)
            status = str(r.status_code)
        except Exception as exc:  # noqa: BLE001 - a slow state must not stop the rest
            status = type(exc).__name__
        elapsed = time.perf_counter() - started
        if elapsed > worst:
            worst, worst_path = elapsed, f"{path} [{status}]"
    return state_id, worst, worst_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default=DEFAULT_BASE)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    with httpx.Client(follow_redirects=True) as client:
        states = [s["id"] for s in client.get(f"{base}/api/geography/states", timeout=60).json()["states"]]
        print(f"warming {len(states)} states via {base}", flush=True)
        started = time.perf_counter()
        done = 0
        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            futures = [pool.submit(warm_state, client, base, s) for s in states]
            for future in concurrent.futures.as_completed(futures):
                state_id, worst, worst_path = future.result()
                done += 1
                print(f"[{done:2}/{len(states)}] {state_id:<32} slowest {worst:6.1f}s  {worst_path}",
                      flush=True)
        print(f"done in {time.perf_counter() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
