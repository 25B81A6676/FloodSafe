"""Dashboard and authority command-centre endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.database import repository
from app.services import dashboard_service, prefetch
from app.services.region_service import RegionNotFound

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary")
async def summary(
    region_id: str | None = None,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """Region-wide risk overview across every monitoring location."""
    try:
        return await dashboard_service.build_summary(region_id, force_refresh=refresh)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")


@router.get("/dashboard/authority")
async def authority(
    region_id: str | None = None,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """Command-centre view: regional summary plus exposed infrastructure."""
    try:
        return await dashboard_service.build_summary(
            region_id, include_infrastructure=True, force_refresh=refresh
        )
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")


@router.get("/dashboard/diagnostics")
async def diagnostics() -> dict[str, Any]:
    """Pipeline diagnostics: stored row counts and cache warm-up status."""
    return {"database": repository.counts(), "prefetch": prefetch.status()}
