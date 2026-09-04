"""Weather endpoints (SOURCE 1)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import historical_service, region_service, weather_service
from app.services.region_service import LocationNotFound

router = APIRouter(tags=["weather"])


@router.get("/weather/{location_id}")
async def get_weather(
    location_id: str,
    refresh: bool = Query(default=False, description="Bypass the cache and refetch"),
) -> dict[str, Any]:
    try:
        loc = region_service.get_location(location_id)
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")

    weather = await weather_service.get_weather(
        loc.id, loc.latitude, loc.longitude, force_refresh=refresh
    )
    antecedent = (await historical_service.get_antecedent_index_batch(
        [(loc.id, loc.latitude, loc.longitude)]
    )).get(loc.id)
    return {"location": loc.to_dict(), "weather": weather, "antecedent": antecedent}


@router.get("/climatology/{location_id}")
async def get_climatology(location_id: str) -> dict[str, Any]:
    """Multi-year ERA5 rainfall climatology used for the anomaly percentile."""
    try:
        loc = region_service.get_location(location_id)
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")

    clim = await historical_service.get_climatology(loc.id, loc.latitude, loc.longitude)
    weather = await weather_service.get_weather(loc.id, loc.latitude, loc.longitude)
    rain_24h = (weather.get("accumulation") or {}).get("rain_24h")
    percentile = historical_service.percentile_of(
        rain_24h, {"distribution": clim.get("_distribution")}
    )
    return {
        "location": loc.to_dict(),
        "climatology": {k: v for k, v in clim.items() if k != "_distribution"},
        "current_rain_24h_mm": rain_24h,
        "current_percentile": percentile,
    }


@router.get("/historical/flood-events")
async def flood_events(region_id: str | None = None) -> dict[str, Any]:
    """Labelled flood-event dataset, if the operator has supplied one.

    The prototype ships without one and does not invent events.
    """
    return historical_service.load_flood_events(region_id)
