"""Region and monitoring-location configuration endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config.settings import settings
from app.services import india_service, osm_service, region_service
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
    region_id: str | None = Query(
        default=None,
        description=(
            "Curated region id, 'india', a state slug ('kerala') or a district "
            "slug ('kerala__wayanad'). Defaults to the pilot region."
        ),
    ),
) -> dict[str, Any]:
    """Selectable locations for any scope.

    Curated regions and state scopes answer from configuration instantly. A
    district scope resolves its real settlements from OpenStreetMap on first
    use and caches them, which is what keeps the location level lazy: nothing
    is fetched for the ~780 districts a user never opens.
    """
    try:
        region = region_service.get_region(region_id)
        locations = [loc.to_dict() for loc in region_service.get_locations(region_id)]
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")

    payload: dict[str, Any] = {
        "region_id": region["id"],
        "region_name": region.get("display_name", region["name"]),
        "scope": region.get("scope", "region"),
        "freshness": "STATIC",
        "notes": [],
    }

    if region.get("scope") == "district":
        district = india_service.get_district(region["id"])
        found = await osm_service.get_district_settlements(district)
        payload["freshness"] = found["freshness"]
        payload["age_minutes"] = found["age_minutes"]
        payload["attribution"] = found["attribution"]
        payload["total_found"] = found["total_found"]
        payload["truncated"] = found["truncated"]
        payload["notes"] = list(found["notes"])
        if found["settlements"]:
            region_service.remember_locations(
                found["settlements"], region_id=region["id"], origin="osm"
            )
            locations = [
                {
                    "id": s["id"],
                    "region_id": region["id"],
                    "name": s["name"],
                    "district": s["district"],
                    "latitude": s["latitude"],
                    "longitude": s["longitude"],
                    "reference_elevation_m": None,
                    "nearest_river": None,
                    "settlement_type": s["settlement_type"],
                    "exposure": None,
                }
                for s in found["settlements"]
            ]
        else:
            # Falls back to the district centroid rather than inventing places.
            payload["notes"].append(
                "No mapped settlements were returned for this district; showing the "
                "district centre, which is derived from the OpenStreetMap boundary."
            )

    payload["count"] = len(locations)
    payload["locations"] = locations
    return payload


@router.get("/locations/{location_id}")
async def get_location(location_id: str) -> dict[str, Any]:
    try:
        return region_service.get_location(location_id).to_dict()
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")
