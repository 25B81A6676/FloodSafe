"""GIS and hydrology endpoints (SOURCE 3)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.services import hydrology_service, osm_service, region_service, risk_map_service
from app.services.region_service import LocationNotFound, RegionNotFound

router = APIRouter(tags=["gis"])


@router.get("/gis/layers")
async def map_layers(region_id: str | None = None) -> dict[str, Any]:
    """Rivers, streams and exposed infrastructure for the map."""
    try:
        return await risk_map_service.get_map_layers(region_id)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")


@router.get("/gis/waterways")
async def waterways(region_id: str | None = None) -> dict[str, Any]:
    try:
        region = region_service.get_region(region_id)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")
    return await osm_service.get_region_waterways(region)


@router.get("/gis/infrastructure")
async def infrastructure(region_id: str | None = None) -> dict[str, Any]:
    try:
        region = region_service.get_region(region_id)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")
    return await osm_service.get_region_infrastructure(region)


@router.get("/hydrology/{location_id}")
async def hydrology(location_id: str) -> dict[str, Any]:
    """GloFAS river discharge plus OSM river-network context."""
    try:
        loc = region_service.get_location(location_id)
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")

    discharge = await hydrology_service.get_hydrology(loc.id, loc.latitude, loc.longitude)
    river = (await osm_service.get_river_context_batch(
        [(loc.id, loc.latitude, loc.longitude)]
    )).get(loc.id)
    return {"location": loc.to_dict(), "discharge": discharge, "river_network": river}
