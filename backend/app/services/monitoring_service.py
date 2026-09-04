"""Pipeline orchestrator.

Runs the whole chain for one or many locations:

    weather + terrain + GIS + hydrology + climatology + antecedent rainfall
        -> validation -> feature engineering -> (optional simulation overrides)
        -> risk engine -> risk score -> classification -> alert -> persistence

All six sources are fetched concurrently and in batch, so assessing 26
locations costs a handful of HTTP requests rather than 150.
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Sequence

from app.config.logging_config import get_logger
from app.database import repository
from app.models.enums import RunMode
from app.services import (
    alert_service,
    elevation_service,
    feature_engineering,
    flood_risk_engine,
    historical_service,
    hydrology_service,
    osm_service,
    region_service,
    simulation_service,
    weather_service,
)
from app.services.data_cache import iso, utcnow
from app.services.feature_engineering import FeatureSet
from app.services.region_service import Location

log = get_logger(__name__)


async def gather_sources(
    locations: Sequence[Location], *, force_refresh: bool = False, compact_hydrology: bool = False
) -> dict[str, dict[str, Any]]:
    """Fetch every source for every location, concurrently and in batch."""
    wpoints = [(l.id, l.latitude, l.longitude) for l in locations]
    tpoints = [(l.id, l.latitude, l.longitude, l.reference_elevation_m) for l in locations]

    weather_t = weather_service.get_weather_batch(wpoints, force_refresh=force_refresh)
    terrain_t = elevation_service.get_terrain_batch(tpoints, force_refresh=force_refresh)
    hydro_t = hydrology_service.get_hydrology_batch(
        wpoints, compact=compact_hydrology, force_refresh=force_refresh
    )
    river_t = osm_service.get_river_context_batch(wpoints)
    clim_t = historical_service.get_climatology_batch(wpoints)
    ante_t = historical_service.get_antecedent_index_batch(wpoints)

    weather, terrain, hydro, river, clim, ante = await asyncio.gather(
        weather_t, terrain_t, hydro_t, river_t, clim_t, ante_t, return_exceptions=True
    )

    def unwrap(result: Any, label: str) -> dict[str, Any]:
        if isinstance(result, BaseException):
            log.error("source '%s' failed entirely: %s", label, result)
            return {}
        return result

    return {
        "weather": unwrap(weather, "weather"),
        "terrain": unwrap(terrain, "terrain"),
        "hydrology": unwrap(hydro, "hydrology"),
        "river": unwrap(river, "river"),
        "climatology": unwrap(clim, "climatology"),
        "antecedent": unwrap(ante, "antecedent"),
    }


def _empty_source(location_id: str, label: str) -> dict[str, Any]:
    return {
        "location_id": location_id, "source": f"{label} unavailable",
        "source_key": label, "freshness": "DEMO", "age_minutes": 0.0,
        "observed_at": iso(utcnow()), "validation": {"ok": False, "errors": [f"{label} unavailable"],
                                                     "warnings": [], "repaired": [], "dropped_fields": []},
        "notes": [f"{label} source returned no data."],
    }


def build_feature_set(
    location: Location, sources: dict[str, dict[str, Any]]
) -> tuple[FeatureSet, dict[str, Any]]:
    lid = location.id
    weather = sources["weather"].get(lid) or _empty_source(lid, "weather")
    terrain = sources["terrain"].get(lid) or _empty_source(lid, "terrain")
    hydro = sources["hydrology"].get(lid) or _empty_source(lid, "hydrology")
    river = sources["river"].get(lid) or _empty_source(lid, "river")
    clim = sources["climatology"].get(lid)
    ante = sources["antecedent"].get(lid)

    fs = feature_engineering.build_features(
        lid, weather=weather, terrain=terrain, hydrology=hydro,
        river_context=river, climatology=clim, antecedent=ante,
    )
    raw = {
        "weather": weather, "terrain": terrain, "hydrology": hydro,
        "river": river, "climatology": clim, "antecedent": ante,
    }
    return fs, raw


def _apply_simulation(fs: FeatureSet, sim_state: dict[str, Any]) -> tuple[FeatureSet, RunMode]:
    if not sim_state.get("active") or not sim_state.get("overrides"):
        return fs, RunMode.LIVE
    feature_engineering.apply_overrides(fs, sim_state["overrides"])
    return fs, RunMode.SIMULATION


# --------------------------------------------------------------------------
# Retrospective and projected risk timeline
# --------------------------------------------------------------------------
def risk_timeline(fs: FeatureSet, weather: dict[str, Any], *, hours_back: int = 24) -> dict[str, Any]:
    """Risk scores over the recent past and the forecast window.

    These are produced by running the real model over the real observed
    rainfall series with terrain and hydrology held at their current values.
    They are *modelled* points, not stored history, and are labelled as such.
    """
    series = (weather.get("series") or {})
    past = [p for p in series.get("past", []) if p.get("precipitation") is not None]
    fut = [p for p in series.get("forecast", []) if p.get("precipitation") is not None]

    if not past:
        return {"past": [], "forecast": [], "method": "unavailable"}

    values = [float(p["precipitation"]) for p in past]
    times = [p["time"] for p in past]

    def roll(end: int, n: int) -> float:
        return round(sum(values[max(0, end - n + 1) : end + 1]), 2)

    points_past: list[dict[str, Any]] = []
    start = max(0, len(values) - hours_back)
    for i in range(start, len(values)):
        clone = _clone_with_rainfall(
            fs,
            intensity=values[i],
            r3=roll(i, 3),
            r24=roll(i, 24),
            forecast24=fs.raw("rainfall_forecast_24h"),
            trend=_slope(values[max(0, i - 5) : i + 1]),
        )
        out = flood_risk_engine.assess(clone)
        points_past.append({
            "time": times[i],
            "risk_score": out["risk_score"],
            "risk_level": out["risk_level"],
            "precipitation": values[i],
            "kind": "modelled",
        })

    points_future: list[dict[str, Any]] = []
    fvals = [float(p["precipitation"]) for p in fut]
    combined = values + fvals
    for j, p in enumerate(fut):
        idx = len(values) + j
        clone = _clone_with_rainfall(
            fs,
            intensity=fvals[j],
            r3=round(sum(combined[max(0, idx - 2) : idx + 1]), 2),
            r24=round(sum(combined[max(0, idx - 23) : idx + 1]), 2),
            forecast24=round(sum(fvals[j : j + 24]), 2),
            trend=_slope(combined[max(0, idx - 5) : idx + 1]),
        )
        out = flood_risk_engine.assess(clone)
        points_future.append({
            "time": p["time"],
            "risk_score": out["risk_score"],
            "risk_level": out["risk_level"],
            "precipitation": fvals[j],
            "probability": p.get("probability"),
            "kind": "projected",
        })

    return {
        "past": points_past,
        "forecast": points_future,
        "method": (
            "Risk recomputed by the same model over the observed hourly rainfall series "
            "(past) and the forecast series (ahead), holding terrain and catchment state "
            "at their current values. Modelled points, not archived assessments."
        ),
    }


def _clone_with_rainfall(
    fs: FeatureSet, *, intensity: float, r3: float, r24: float,
    forecast24: float | None, trend: float,
) -> FeatureSet:
    clone = FeatureSet(
        location_id=fs.location_id,
        features={k: copy.copy(v) for k, v in fs.features.items()},
        context=fs.context,
        warnings=[],
    )
    for key, value in (
        ("rainfall_intensity", intensity),
        ("rainfall_3h", r3),
        ("rainfall_24h", r24),
        ("rainfall_forecast_24h", forecast24),
        ("rainfall_trend", trend),
    ):
        fv = clone.features.get(key)
        if fv is not None and value is not None:
            fv.raw = float(value)
            fv.available = True
    return clone


def _slope(values: Sequence[float]) -> float:
    pts = list(enumerate(values))
    n = len(pts)
    if n < 3:
        return 0.0
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    num = sum((x - mx) * (y - my) for x, y in pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    return round(num / den, 3) if den else 0.0


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------
async def assess_locations(
    locations: Sequence[Location],
    *,
    force_refresh: bool = False,
    persist: bool = True,
    compact_hydrology: bool = False,
) -> list[dict[str, Any]]:
    """Full pipeline for a set of locations."""
    if not locations:
        return []

    sources = await gather_sources(
        locations, force_refresh=force_refresh, compact_hydrology=compact_hydrology
    )
    sim_state = simulation_service.current_state()
    results: list[dict[str, Any]] = []

    for loc in locations:
        fs, raw = build_feature_set(loc, sources)
        fs, mode = _apply_simulation(fs, sim_state)

        previous = repository.latest_risk(loc.id, mode=mode.value)
        risk = flood_risk_engine.assess(
            fs,
            mode=mode,
            previous_score=previous["risk_score"] if previous else None,
            scenario_id=sim_state.get("scenario_id") if mode == RunMode.SIMULATION else None,
        )
        alert = alert_service.build_alert(risk, loc.to_dict())

        if persist:
            try:
                repository.save_risk(loc.id, risk)
                repository.save_weather(loc.id, raw["weather"])
                repository.save_terrain(loc.id, raw["terrain"])
                repository.save_hydrology(loc.id, raw["hydrology"], raw["river"])
            except Exception as exc:  # noqa: BLE001 - persistence must not break a response
                log.warning("could not persist observations for %s: %s", loc.id, exc)

        results.append({
            "location": loc.to_dict(),
            "risk": risk,
            "alert": alert,
            "features": fs.to_dict(),
            "sources": raw,
            "mode": mode.value,
        })

    return results


async def get_monitoring(
    location_id: str, *, force_refresh: bool = False, include_timeline: bool = True
) -> dict[str, Any]:
    """Complete current status for one monitoring location."""
    location = region_service.get_location(location_id)
    results = await assess_locations([location], force_refresh=force_refresh)
    result = results[0]

    raw = result["sources"]
    weather, terrain = raw["weather"], raw["terrain"]
    hydro, river = raw["hydrology"], raw["river"]
    sim_state = simulation_service.current_state()

    timeline = (
        risk_timeline(
            feature_engineering.build_features(
                location_id, weather=weather, terrain=terrain, hydrology=hydro,
                river_context=river, climatology=raw["climatology"], antecedent=raw["antecedent"],
            ),
            weather,
        )
        if include_timeline else {"past": [], "forecast": [], "method": "skipped"}
    )

    freshness = _freshness_block(raw)
    return {
        "location": result["location"],
        "region_id": location.region_id,
        "risk": result["risk"],
        "alert": result["alert"],
        "weather": weather,
        "terrain": terrain,
        "hydrology": hydro,
        "river_context": river,
        "climatology": {k: v for k, v in (raw["climatology"] or {}).items() if k != "_distribution"},
        "antecedent": raw["antecedent"],
        "features": result["features"],
        "timeline": timeline,
        "stored_history": repository.risk_history(location_id, limit=48, mode=result["mode"]),
        "simulation": {
            "active": sim_state.get("active", False),
            "scenario_id": sim_state.get("scenario_id"),
            "scenario_name": (sim_state.get("scenario") or {}).get("name"),
            "overrides": sim_state.get("overrides", {}),
            "readouts": (
                simulation_service.derived_readouts(sim_state.get("overrides", {}))
                if sim_state.get("active") else None
            ),
        },
        "data_freshness": freshness,
        "mode": result["mode"],
        "generated_at": iso(utcnow()),
    }


def _freshness_block(raw: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for label, key in (
        ("Weather", "weather"), ("Terrain", "terrain"), ("River discharge", "hydrology"),
        ("River network", "river"), ("Rainfall climatology", "climatology"),
        ("Antecedent rainfall", "antecedent"),
    ):
        src = raw.get(key) or {}
        rows.append({
            "label": label,
            "source": src.get("source"),
            "source_key": src.get("source_key"),
            "freshness": src.get("freshness", "DEMO"),
            "age_minutes": src.get("age_minutes"),
            "attribution": src.get("attribution"),
            "notes": src.get("notes", []),
        })
    live = sum(1 for r in rows if r["freshness"] == "LIVE")
    cached = sum(1 for r in rows if r["freshness"] == "CACHED")
    degraded = sum(1 for r in rows if r["freshness"] in {"STALE_CACHE", "DEMO"})
    return {
        "sources": rows,
        "live": live,
        "cached": cached,
        "degraded": degraded,
        "total": len(rows),
        "overall": "LIVE" if degraded == 0 and live else ("CACHED" if degraded == 0 else "DEGRADED"),
    }
