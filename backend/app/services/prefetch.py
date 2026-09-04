"""Background cache warm-up.

Runs once at startup so the first dashboard load is fast and so the platform
has real cached data to fall back on if an upstream source later fails. Every
step is independently guarded: a warm-up failure must never prevent the API
from serving.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from app.config.logging_config import get_logger
from app.config.settings import network_disabled
from app.services import (
    elevation_service,
    historical_service,
    hydrology_service,
    osm_service,
    region_service,
    simulation_service,
    weather_service,
)

log = get_logger(__name__)

_status: dict[str, Any] = {"state": "idle", "steps": [], "started_at": None, "finished_at": None}


def status() -> dict[str, Any]:
    return dict(_status)


async def _step(name: str, coro_factory: Callable[[], Awaitable[Any]]) -> None:
    started = time.perf_counter()
    try:
        await coro_factory()
        elapsed = (time.perf_counter() - started) * 1000
        _status["steps"].append({"name": name, "ok": True, "ms": round(elapsed)})
        log.info("prefetch %-22s ok   %6.0f ms", name, elapsed)
    except Exception as exc:  # noqa: BLE001 - warm-up is strictly best effort
        elapsed = (time.perf_counter() - started) * 1000
        _status["steps"].append({"name": name, "ok": False, "ms": round(elapsed), "error": str(exc)[:200]})
        log.warning("prefetch %-22s FAIL %6.0f ms: %s", name, elapsed, str(exc)[:160])


async def warm_cache() -> dict[str, Any]:
    """Warm the cache for the default region. Safe to call more than once."""
    if network_disabled():
        log.info("prefetch skipped: outbound network disabled")
        _status.update(state="skipped", steps=[])
        return status()

    _status.update(state="running", steps=[], started_at=time.time(), finished_at=None)
    await asyncio.sleep(1.0)  # let the server finish binding before we start

    try:
        simulation_service.load_scenarios()
        region = region_service.get_region()
        locations = region_service.get_locations(region["id"])
    except Exception as exc:  # noqa: BLE001
        log.error("prefetch aborted, configuration unreadable: %s", exc)
        _status.update(state="failed", finished_at=time.time())
        return status()

    points = [(l.id, l.latitude, l.longitude) for l in locations]
    tpoints = [(l.id, l.latitude, l.longitude, l.reference_elevation_m) for l in locations]
    cells = region_service.get_grid_cells(region["id"])
    cell_points = [(c.id, c.center_lat, c.center_lon) for c in cells]
    cell_tpoints = [(c.id, c.center_lat, c.center_lon, None) for c in cells]

    # Terrain first: it is the most expensive to derive and effectively never
    # changes, so caching it delivers the largest lasting benefit.
    await _step("terrain/locations", lambda: elevation_service.get_terrain_batch(tpoints))
    await _step("terrain/grid", lambda: elevation_service.get_terrain_batch(cell_tpoints))
    await _step("weather/locations", lambda: weather_service.get_weather_batch(points))
    await _step("weather/grid", lambda: weather_service.get_weather_batch(cell_points))
    await _step("antecedent/locations", lambda: historical_service.get_antecedent_index_batch(points))
    await _step("antecedent/grid", lambda: historical_service.get_antecedent_index_batch(cell_points))
    await _step("glofas/locations", lambda: hydrology_service.get_hydrology_batch(points))
    await _step("glofas/grid", lambda: hydrology_service.get_hydrology_batch(cell_points, compact=True))
    await _step("osm/waterways", lambda: osm_service.get_region_waterways(region))
    await _step("osm/infrastructure", lambda: osm_service.get_region_infrastructure(region))
    await _step("osm/river-proximity", lambda: osm_service.get_river_context_batch(points))
    await _step("era5/climatology", lambda: historical_service.get_climatology_batch(points))

    ok = sum(1 for s in _status["steps"] if s["ok"])
    _status.update(state="done", finished_at=time.time())
    log.info("prefetch complete: %d/%d steps succeeded", ok, len(_status["steps"]))
    return status()
