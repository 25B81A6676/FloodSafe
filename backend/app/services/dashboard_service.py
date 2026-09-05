"""Region-level aggregation for the dashboard and authority command centre."""
from __future__ import annotations

import asyncio
from typing import Any

from app.config.logging_config import get_logger
from app.services import (
    alert_service,
    flood_risk_engine,
    http_client,
    monitoring_service,
    osm_service,
    region_service,
    risk_config,
    simulation_service,
)
from app.services.data_cache import iso, utcnow

log = get_logger(__name__)


async def build_summary(
    region_id: str | None = None, *, include_infrastructure: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    region = region_service.get_region(region_id)
    locations = region_service.get_locations(region["id"])

    assessments_t = monitoring_service.assess_locations(locations, force_refresh=force_refresh)
    if include_infrastructure:
        assessments, infra = await asyncio.gather(
            assessments_t, osm_service.get_region_infrastructure(region),
            return_exceptions=True,
        )
        if isinstance(infra, BaseException):
            log.error("infrastructure lookup failed: %s", infra)
            infra = {"features": [], "counts": {}, "freshness": "DEMO", "notes": []}
    else:
        assessments = await assessments_t
        infra = None

    if isinstance(assessments, BaseException):
        raise assessments

    rows: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    scores: list[float] = []
    levels: list[str] = []

    for item in assessments:
        risk, loc = item["risk"], item["location"]
        ctx = risk.get("context", {})
        rows.append({
            "location_id": loc["id"],
            "name": loc["name"],
            "district": loc["district"],
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "settlement_type": loc.get("settlement_type"),
            "nearest_river": loc.get("nearest_river"),
            "risk_score": risk["risk_score"],
            "risk_level": risk["risk_level"],
            "risk_color": risk["risk_color"],
            "confidence": risk["confidence"],
            "trend": risk["trend"],
            "score_delta": risk.get("score_delta"),
            "top_factor": (risk.get("contributors") or [{}])[0].get("factor"),
            "rainfall_mm_h": (ctx.get("rainfall") or {}).get("current_mm_h"),
            "rain_24h_mm": (ctx.get("rainfall") or {}).get("rain_24h"),
            "forecast_24h_mm": (ctx.get("rainfall") or {}).get("forecast_24h"),
            "elevation_m": (ctx.get("terrain") or {}).get("elevation_m"),
            "slope_deg": (ctx.get("terrain") or {}).get("slope_deg"),
            "river_status": (ctx.get("hydrology") or {}).get("river_status"),
            "discharge_ratio": (ctx.get("hydrology") or {}).get("discharge_ratio"),
            "river_distance_m": (ctx.get("hydrology") or {}).get("river_distance_m"),
            "saturation": (ctx.get("soil") or {}).get("saturation_class"),
            "alert_severity": item["alert"]["severity"],
            "mode": risk["mode"],
        })
        alerts.append(item["alert"])
        scores.append(float(risk["risk_score"]))
        levels.append(risk["risk_level"])

    rows.sort(key=lambda r: r["risk_score"], reverse=True)
    distribution = flood_risk_engine.distribution(levels)
    sim_state = simulation_service.current_state()

    rainfall_values = [r["rainfall_mm_h"] for r in rows if r["rainfall_mm_h"] is not None]
    rain24_values = [r["rain_24h_mm"] for r in rows if r["rain_24h_mm"] is not None]
    forecast_values = [r["forecast_24h_mm"] for r in rows if r["forecast_24h_mm"] is not None]

    wettest = max(rows, key=lambda r: (r["rain_24h_mm"] or -1)) if rows else None
    freshness_counts: dict[str, int] = {}
    for item in assessments:
        for state, n in (item["risk"]["feature_summary"]["freshness"] or {}).items():
            freshness_counts[state] = freshness_counts.get(state, 0) + n

    summary = {
        "region": {
            "id": region["id"],
            "name": region.get("display_name", region["name"]),
            "center": region["center"],
            "bbox": region["bbox"],
            "default_zoom": region.get("default_zoom", 8),
            "terrain_type": region.get("terrain_type"),
            "scope": region.get("scope", "region"),
            "state_id": region.get("state_id"),
            "state_name": region.get("state_name"),
        },
        # What one ranked row represents at this scope. A national summary ranks
        # states, a state summary ranks districts, and everything else ranks
        # individual monitoring locations - all from the same rows, because the
        # monitoring locations of a scope *are* its children.
        "row_kind": {
            "national": "state",
            "state": "district",
            "district": "location",
        }.get(region.get("scope", ""), "location"),
        "generated_at": iso(utcnow()),
        "mode": "SIMULATION" if sim_state.get("active") else "LIVE",
        "scenario_id": sim_state.get("scenario_id"),
        "scenario_name": (sim_state.get("scenario") or {}).get("name"),
        "totals": {
            "monitoring_locations": len(rows),
            **{k.lower(): v for k, v in distribution.items()},
            "distribution": distribution,
            "at_or_above_high": distribution.get("HIGH", 0) + distribution.get("EXTREME", 0),
        },
        "risk_summary": flood_risk_engine.score_summary(scores),
        "highest_risk": rows[0] if rows else None,
        "rainfall_summary": {
            "max_intensity_mm_h": round(max(rainfall_values), 2) if rainfall_values else None,
            "mean_intensity_mm_h": (
                round(sum(rainfall_values) / len(rainfall_values), 2) if rainfall_values else None
            ),
            "max_24h_mm": round(max(rain24_values), 1) if rain24_values else None,
            "mean_24h_mm": (
                round(sum(rain24_values) / len(rain24_values), 1) if rain24_values else None
            ),
            "max_forecast_24h_mm": round(max(forecast_values), 1) if forecast_values else None,
            "wettest_location": (
                {"name": wettest["name"], "rain_24h_mm": wettest["rain_24h_mm"]}
                if wettest and wettest["rain_24h_mm"] is not None else None
            ),
        },
        "locations": rows,
        "alerts": alert_service.summarize(alerts),
        "legend": risk_config.all_risk_classes(),
        "data_freshness": {
            "feature_states": freshness_counts,
            "sources": http_client.all_health(),
        },
        "simulation": {
            "active": sim_state.get("active", False),
            "scenario_id": sim_state.get("scenario_id"),
            "overrides": sim_state.get("overrides", {}),
        },
        "attribution": [
            "Weather data by Open-Meteo.com (CC BY 4.0)",
            "River discharge from Copernicus EMS GloFAS via Open-Meteo",
            "Historical rainfall from ECMWF ERA5 via Open-Meteo",
            "Elevation from Copernicus DEM GLO-90 via Open-Meteo",
            "Map data (c) OpenStreetMap contributors, ODbL",
        ],
    }

    if infra is not None:
        counts = infra.get("counts", {})
        summary["infrastructure"] = {
            "counts": counts,
            "total": sum(counts.values()),
            "freshness": infra.get("freshness"),
            "notes": infra.get("notes", []),
            "features": infra.get("features", []),
        }
        summary["exposure"] = _exposure(rows, infra.get("features", []))

    return summary


def _exposure(rows: list[dict[str, Any]], features: list[dict[str, Any]]) -> dict[str, Any]:
    """Count mapped facilities lying near locations currently at high risk.

    A deliberately simple proximity count over real OSM features - useful for
    prioritisation, and not presented as a casualty or damage estimate.
    """
    from app.services.region_service import haversine_m

    high = [r for r in rows if r["risk_level"] in {"HIGH", "EXTREME"}]
    radius_m = 10_000.0
    near: dict[str, int] = {}
    matched: list[dict[str, Any]] = []

    for f in features:
        for r in high:
            d = haversine_m(f["latitude"], f["longitude"], r["latitude"], r["longitude"])
            if d <= radius_m:
                near[f["kind"]] = near.get(f["kind"], 0) + 1
                matched.append({**f, "near_location": r["name"], "distance_m": round(d)})
                break

    return {
        "radius_km": radius_m / 1000.0,
        "high_risk_locations": len(high),
        "counts": near,
        "total": sum(near.values()),
        "features": matched[:250],
        "note": (
            "Count of OpenStreetMap facilities within 10 km of a location currently "
            "assessed as HIGH or EXTREME. A prioritisation aid, not a damage or "
            "casualty estimate."
        ),
    }
