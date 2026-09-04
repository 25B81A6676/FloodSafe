"""Complete per-location monitoring status."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import monitoring_service
from app.services.region_service import LocationNotFound

router = APIRouter(tags=["monitoring"])


@router.get("/monitoring/{location_id}")
async def monitoring(
    location_id: str,
    refresh: bool = Query(default=False, description="Bypass caches and refetch every source"),
    timeline: bool = Query(default=True, description="Include the modelled risk timeline"),
) -> dict[str, Any]:
    """Everything the dashboard needs for one location in a single response:
    weather, terrain, hydrology, GIS context, risk, alert and data freshness."""
    try:
        return await monitoring_service.get_monitoring(
            location_id, force_refresh=refresh, include_timeline=timeline
        )
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")
