"""Alert endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import alert_service, dashboard_service, monitoring_service, region_service
from app.services.region_service import LocationNotFound, RegionNotFound

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def alerts(
    region_id: str | None = None,
    min_severity: str = Query(
        default="ADVISORY",
        description="One of INFO, ADVISORY, WARNING, CRITICAL",
    ),
) -> dict[str, Any]:
    """Active advisories across the region, most severe first."""
    try:
        summary = await dashboard_service.build_summary(region_id)
    except RegionNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown region '{region_id}'")

    threshold = alert_service.severity_rank(min_severity.upper())
    block = summary["alerts"]
    filtered = [
        a for a in block["alerts"]
        if alert_service.severity_rank(a["severity"]) >= threshold
    ]
    return {
        "region_id": summary["region"]["id"],
        "mode": summary["mode"],
        "min_severity": min_severity.upper(),
        "count": len(filtered),
        "total_assessed": block["total"],
        "by_severity": block["by_severity"],
        "highest_severity": block["highest_severity"],
        "alerts": filtered,
        "advisory_footer": block["advisory_footer"],
        "generated_at": block["generated_at"],
    }


@router.get("/alerts/{location_id}")
async def alert_for_location(location_id: str) -> dict[str, Any]:
    try:
        loc = region_service.get_location(location_id)
    except LocationNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")

    results = await monitoring_service.assess_locations([loc])
    return results[0]["alert"]
