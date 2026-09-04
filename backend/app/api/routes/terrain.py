"""Terrain endpoints (SOURCE 2)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import elevation_service, region_service
from app.services.region_service import LocationNotFound, RegionNotFound

router = APIRouter(tags=["terrain"])


@router.get("/terrain/{location_id}")
async def get_terrain(
    location_id: str,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        loc = region_service.get_location(location_id)
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")

    terrain = await elevation_service.get_terrain(
        loc.id, loc.latitude, loc.longitude,
        reference_elevation_m=loc.reference_elevation_m, force_refresh=refresh,
    )
    return {"location": loc.to_dict(), "terrain": terrain}


@router.get("/terrain")
async def get_terrain_all(region_id: str | None = None) -> dict[str, Any]:
    """Terrain for every monitoring location in a region."""
    try:
        locations = region_service.get_locations(region_id)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")

    results = await elevation_service.get_terrain_batch(
        [(l.id, l.latitude, l.longitude, l.reference_elevation_m) for l in locations]
    )
    return {
        "count": len(results),
        "terrain": [
            {**results[l.id], "name": l.name, "district": l.district}
            for l in locations if l.id in results
        ],
    }
