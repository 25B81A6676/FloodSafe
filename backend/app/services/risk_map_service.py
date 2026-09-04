"""Spatial risk layer.

The map layer is a grid of *independent assessments*, not an interpolated
surface. Each cell centre gets its own real weather, its own real DEM terrain,
its own real GloFAS discharge and its own real distance to the OSM river
network, and is then scored by the same model as any monitoring location.

Cells are drawn as discrete rectangles precisely because the underlying data is
discrete. Smoothing them into a continuous heat surface would imply spatial
precision the inputs do not support.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.config.logging_config import get_logger
from app.models.enums import RunMode
from app.services import (
    elevation_service,
    feature_engineering,
    flood_risk_engine,
    historical_service,
    hydrology_service,
    osm_service,
    region_service,
    risk_config,
    simulation_service,
    weather_service,
)
from app.services.data_cache import iso, utcnow

log = get_logger(__name__)

GRID_SEARCH_RADIUS_M = 4000.0


async def build_risk_map(
    region_id: str | None = None, *, force_refresh: bool = False
) -> dict[str, Any]:
    region = region_service.get_region(region_id)
    cells = region_service.get_grid_cells(region["id"])
    points = [(c.id, c.center_lat, c.center_lon) for c in cells]
    tpoints = [(c.id, c.center_lat, c.center_lon, None) for c in cells]

    weather_t = weather_service.get_weather_batch(points, force_refresh=force_refresh)
    terrain_t = elevation_service.get_terrain_batch(tpoints, force_refresh=force_refresh)
    hydro_t = hydrology_service.get_hydrology_batch(points, compact=True, force_refresh=force_refresh)
    ante_t = historical_service.get_antecedent_index_batch(points)
    water_t = osm_service.get_region_waterways(region)

    weather, terrain, hydro, ante, waterways = await asyncio.gather(
        weather_t, terrain_t, hydro_t, ante_t, water_t, return_exceptions=True
    )

    def unwrap(v: Any, label: str) -> Any:
        if isinstance(v, BaseException):
            log.error("risk map source '%s' failed: %s", label, v)
            return {} if label != "waterways" else {"rivers": [], "streams": [], "freshness": "DEMO"}
        return v

    weather = unwrap(weather, "weather")
    terrain = unwrap(terrain, "terrain")
    hydro = unwrap(hydro, "hydrology")
    ante = unwrap(ante, "antecedent")
    waterways = unwrap(waterways, "waterways")

    river_ctx = osm_service.river_context_from_dataset(
        points, waterways, radius_m=GRID_SEARCH_RADIUS_M
    )

    sim_state = simulation_service.current_state()
    mode = RunMode.SIMULATION if sim_state.get("active") else RunMode.LIVE

    out_cells: list[dict[str, Any]] = []
    for cell in cells:
        w = weather.get(cell.id) or {}
        t = terrain.get(cell.id) or {}
        h = hydro.get(cell.id) or {}
        r = river_ctx.get(cell.id) or {}
        a = ante.get(cell.id) or {}

        fs = feature_engineering.build_features(
            cell.id, weather=w, terrain=t, hydrology=h,
            river_context=r, climatology=None, antecedent=a,
        )
        if mode == RunMode.SIMULATION:
            feature_engineering.apply_overrides(fs, sim_state.get("overrides", {}))

        risk = flood_risk_engine.assess(fs, mode=mode)
        top = (risk.get("contributors") or [{}])[0]

        out_cells.append({
            **cell.to_dict(),
            "risk_score": risk["risk_score"],
            "risk_level": risk["risk_level"],
            "risk_color": risk["risk_color"],
            "confidence": risk["confidence"],
            "top_factor": top.get("factor"),
            "top_factor_value": top.get("display_value"),
            "rainfall_mm_h": (w.get("current") or {}).get("precipitation_mm_h"),
            "rain_24h_mm": (w.get("accumulation") or {}).get("rain_24h"),
            "forecast_24h_mm": (w.get("forecast") or {}).get("rain_24h"),
            "elevation_m": t.get("elevation_m"),
            "slope_deg": t.get("slope_deg"),
            "river_distance_m": r.get("river_distance_m"),
            "nearest_waterway": r.get("nearest_waterway_name"),
            "discharge_ratio": h.get("discharge_ratio"),
            "discharge_m3s": h.get("discharge_m3s"),
            "freshness": w.get("freshness", "DEMO"),
            "features_available": risk["feature_summary"]["available"],
            "features_total": risk["feature_summary"]["total"],
        })

    scores = [c["risk_score"] for c in out_cells]
    levels = [c["risk_level"] for c in out_cells]
    highest = max(out_cells, key=lambda c: c["risk_score"]) if out_cells else None

    log.info(
        "risk map for %s: %d cells, mean %.1f, max %.1f (%s)",
        region["id"], len(out_cells),
        (sum(scores) / len(scores)) if scores else 0.0,
        max(scores) if scores else 0.0, mode.value,
    )

    return {
        "region_id": region["id"],
        "region_name": region.get("display_name", region["name"]),
        "generated_at": iso(utcnow()),
        "mode": mode.value,
        "scenario_id": sim_state.get("scenario_id"),
        "grid": {
            "rows": region.get("risk_grid", {}).get("rows"),
            "cols": region.get("risk_grid", {}).get("cols"),
            "bbox": region.get("risk_grid", {}).get("bbox", region["bbox"]),
            "cell_count": len(out_cells),
        },
        "cells": out_cells,
        "summary": {
            **flood_risk_engine.score_summary([float(s) for s in scores]),
            "distribution": flood_risk_engine.distribution(levels),
            "highest_cell": highest,
        },
        "legend": risk_config.all_risk_classes(),
        "method": (
            "Each cell is scored independently from its own real weather, DEM terrain, "
            "GloFAS discharge and OpenStreetMap river distance. Cells are discrete "
            "assessments, not an interpolated surface. The ERA5 rainfall-anomaly feature "
            "is evaluated for monitoring locations only, so grid cells use 11 of 12 "
            "features and the model renormalises accordingly."
        ),
        "attribution": [
            "Weather data by Open-Meteo.com (CC BY 4.0)",
            "River discharge from Copernicus EMS GloFAS via Open-Meteo",
            "Elevation from Copernicus DEM GLO-90 via Open-Meteo",
            "Map data (c) OpenStreetMap contributors, ODbL",
        ],
    }


async def get_map_layers(region_id: str | None = None) -> dict[str, Any]:
    """Vector layers for the map: rivers, streams and exposed infrastructure."""
    region = region_service.get_region(region_id)
    waterways, infrastructure = await asyncio.gather(
        osm_service.get_region_waterways(region),
        osm_service.get_region_infrastructure(region),
        return_exceptions=True,
    )
    if isinstance(waterways, BaseException):
        log.error("waterway layer failed: %s", waterways)
        waterways = {"rivers": [], "streams": [], "freshness": "DEMO", "count": 0,
                     "notes": ["Waterway layer unavailable."], "named_rivers": []}
    if isinstance(infrastructure, BaseException):
        log.error("infrastructure layer failed: %s", infrastructure)
        infrastructure = {"features": [], "counts": {}, "freshness": "DEMO",
                          "notes": ["Infrastructure layer unavailable."]}

    # Trim the stream layer for transport: keep the whole river network, and the
    # longest streams up to a budget, so the map stays responsive.
    streams = sorted(
        waterways.get("streams", []), key=lambda s: len(s.get("coordinates", [])), reverse=True
    )[:700]

    return {
        "region_id": region["id"],
        "bbox": waterways.get("bbox", region["bbox"]),
        "rivers": waterways.get("rivers", []),
        "streams": streams,
        "named_rivers": waterways.get("named_rivers", []),
        "waterway_freshness": waterways.get("freshness"),
        "waterway_age_minutes": waterways.get("age_minutes"),
        "waterway_notes": waterways.get("notes", []),
        "stream_count_total": len(waterways.get("streams", [])),
        "stream_count_returned": len(streams),
        "infrastructure": infrastructure.get("features", []),
        "infrastructure_counts": infrastructure.get("counts", {}),
        "infrastructure_freshness": infrastructure.get("freshness"),
        "infrastructure_notes": infrastructure.get("notes", []),
        "attribution": "Map data (c) OpenStreetMap contributors, ODbL",
        "generated_at": iso(utcnow()),
    }
