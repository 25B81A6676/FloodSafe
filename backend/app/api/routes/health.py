"""Health, source connectivity and cache diagnostics."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.config.settings import network_disabled, settings
from app.services import data_cache, http_client
from app.services.data_cache import iso, utcnow

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "timestamp": iso(utcnow()),
        "network_enabled": not network_disabled(),
        "default_region": settings.default_region_id,
    }


@router.get("/system/sources")
async def sources() -> dict[str, Any]:
    """Live connectivity status of every external data source.

    Powers the status indicator in the UI so a degraded feed is visible rather
    than silently replaced.
    """
    health_rows = http_client.all_health()
    down = sum(1 for r in health_rows if r["status"] == "DOWN")
    degraded = sum(1 for r in health_rows if r["status"] == "DEGRADED")
    overall = "DOWN" if down and down == len(health_rows) else (
        "DEGRADED" if (down or degraded) else "OK"
    )
    return {
        "overall": overall,
        "network_enabled": not network_disabled(),
        "sources": sorted(health_rows, key=lambda r: r["name"]),
        "cache": data_cache.stats(),
        "timestamp": iso(utcnow()),
    }


@router.get("/system/cache")
async def cache_stats() -> dict[str, Any]:
    return data_cache.stats()


@router.get("/system/providers")
async def providers() -> dict[str, Any]:
    """Declared data-provider families and their integration status.

    Deliberately explicit about what is NOT integrated: a family lists official
    Indian sources that were investigated as 'candidate', with the reason they
    are not consumed. Silence would read as capability we do not have.
    """
    path = settings.config_dir / "providers.json"
    if not path.exists():
        raise HTTPException(status_code=503, detail="Provider manifest not found")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for family in manifest.get("families", []):
        for provider in family.get("providers", []):
            counts[provider["status"]] = counts.get(provider["status"], 0) + 1
    return {**manifest, "counts": counts}
