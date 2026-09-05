"""India administrative hierarchy: country -> state -> district.

The location level is deliberately *not* here - it is served by the existing
``/locations`` endpoint, which already resolves locations for any scope. One
endpoint per concept beats two paths to the same data.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.services import india_service
from app.services.india_service import GeographyUnavailable, ScopeNotFound

router = APIRouter(tags=["configuration"])

_MISSING = (
    "India geography dataset is not present. Generate it with "
    "'python scripts/build_india_geo.py' (fetches administrative boundaries "
    "from OpenStreetMap)."
)


@router.get("/geography")
async def geography() -> dict[str, Any]:
    """Dataset provenance and the country node of the hierarchy."""
    info = india_service.dataset_info()
    if not info.get("available"):
        raise HTTPException(status_code=503, detail=_MISSING)
    return {
        "country": {"id": india_service.NATIONAL_ID, "name": "India"},
        "bbox": india_service.national_bbox(),
        "levels": ["country", "state", "district", "location"],
        **info,
    }


@router.get("/geography/states")
async def list_states() -> dict[str, Any]:
    states = india_service.list_states()
    if not states:
        raise HTTPException(status_code=503, detail=_MISSING)
    return {
        "country": "India",
        "count": len(states),
        "states": states,
        "attribution": india_service.dataset_info().get("attribution"),
    }


@router.get("/geography/states/{state_id}/districts")
async def list_districts(state_id: str) -> dict[str, Any]:
    try:
        state = india_service.get_state(state_id)
        districts = india_service.list_districts(state_id)
    except ScopeNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown state '{state_id}'")
    except GeographyUnavailable:
        raise HTTPException(status_code=503, detail=_MISSING)
    return {
        "state_id": state["id"],
        "state_name": state["name"],
        "count": len(districts),
        "districts": districts,
        "attribution": india_service.dataset_info().get("attribution"),
    }
