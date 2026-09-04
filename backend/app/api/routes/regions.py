"""Region and monitoring-location configuration endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config.settings import settings
from app.services import region_service
from app.services.region_service import LocationNotFound, RegionNotFound

router = APIRouter(tags=["configuration"])


@router.get("/regions")
async def list_regions() -> dict[str, Any]:
    return {
        "default_region_id": settings.default_region_id,
        "count": len(region_service.list_regions()),
        "regions": region_service.list_regions(),
    }


@router.get("/regions/{region_id}")
async def get_region(region_id: str) -> dict[str, Any]:
    try:
        region = region_service.get_region(region_id)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")
    return {
        **{k: v for k, v in region.items() if k != "monitoring_locations"},
        "location_count": len(region.get("monitoring_locations", [])),
        "grid_cells": [c.to_dict() for c in region_service.get_grid_cells(region_id)],
    }


@router.get("/locations")
async def list_locations(
    region_id: str | None = Query(default=None, description="Defaults to the pilot region"),
) -> dict[str, Any]:
    try:
        locations = region_service.get_locations(region_id)
        region = region_service.get_region(region_id)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")
    return {
        "region_id": region["id"],
        "region_name": region.get("display_name", region["name"]),
        "count": len(locations),
        "locations": [loc.to_dict() for loc in locations],
    }


@router.get("/locations/{location_id}")
async def get_location(location_id: str) -> dict[str, Any]:
    try:
        return region_service.get_location(location_id).to_dict()
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")
