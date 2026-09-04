"""Risk endpoints.

Route order matters: ``/risk/map`` and ``/risk/model`` are declared before
``/risk/{location_id}`` so they are not captured by the path parameter.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import monitoring_service, region_service, risk_config, risk_map_service, risk_models
from app.services.region_service import LocationNotFound, RegionNotFound

router = APIRouter(tags=["risk"])


@router.get("/risk/map")
async def risk_map(
    region_id: str | None = None,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """Grid of independent per-cell risk assessments for the map layer."""
    try:
        return await risk_map_service.build_risk_map(region_id, force_refresh=refresh)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")


@router.get("/risk/model")
async def risk_model_config() -> dict[str, Any]:
    """Full transparency on how the score is produced."""
    cfg = risk_config.get_config()
    features = cfg.get("features", {})
    return {
        "model_id": cfg.get("model_id"),
        "model_name": cfg.get("model_name"),
        "version": cfg.get("version"),
        "description": cfg.get("description"),
        "methodology_note": cfg.get("methodology_note"),
        "feature_count": len(features),
        "weight_sum": round(sum(f.get("weight", 0) for f in features.values()), 4),
        "features": [
            {
                "key": key,
                "label": f.get("label"),
                "group": f.get("group"),
                "unit": f.get("unit"),
                "weight": f.get("weight"),
                "direction": f.get("direction"),
                "curve": f.get("curve"),
                "rationale": f.get("rationale"),
            }
            for key, f in features.items()
        ],
        "risk_classes": cfg.get("risk_classes"),
        "confidence_rules": cfg.get("confidence_rules"),
        "impact_thresholds": cfg.get("impact_thresholds"),
        "available_models": risk_models.available_models(),
        "pipeline": [
            "Multi-source acquisition (weather, terrain, hydrology, GIS, climatology)",
            "Validation and normalisation",
            "Feature engineering",
            "Weighted risk model",
            "Risk score 0-100 and classification",
            "Spatial visualisation and alerting",
        ],
    }


@router.get("/risk/{location_id}")
async def get_risk(location_id: str, refresh: bool = Query(default=False)) -> dict[str, Any]:
    try:
        loc = region_service.get_location(location_id)
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")

    results = await monitoring_service.assess_locations([loc], force_refresh=refresh)
    result = results[0]
    return {
        "location": result["location"],
        "risk": result["risk"],
        "alert": result["alert"],
        "features": result["features"],
        "mode": result["mode"],
    }
