"""Simulation endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas.simulation import SimulationRunRequest
from app.services import alert_dispatch, monitoring_service, region_service, simulation_service
from app.services.region_service import LocationNotFound

router = APIRouter(tags=["simulation"])


@router.get("/simulation/scenarios")
async def scenarios() -> dict[str, Any]:
    items = simulation_service.list_scenarios()
    return {
        "count": len(items),
        "scenarios": items,
        "notice": (
            "Scenarios are demonstration inputs, not measurements. Activating one "
            "replaces the corresponding model features and marks them SIMULATION "
            "everywhere they appear."
        ),
    }


@router.get("/simulation/controls")
async def controls() -> dict[str, Any]:
    return {
        "controls": simulation_service.controls(),
        "note": (
            "Every control maps directly to a weighted model feature. Moving a slider "
            "changes the model input; it does not scale the output score."
        ),
    }


@router.get("/simulation/state")
async def state() -> dict[str, Any]:
    st = simulation_service.current_state()
    return {
        **st,
        "readouts": (
            simulation_service.derived_readouts(st.get("overrides", {}))
            if st.get("active") else None
        ),
    }


@router.post("/simulation/run")
async def run(request: SimulationRunRequest) -> dict[str, Any]:
    """Activate a scenario and/or manual overrides, and optionally return the
    freshly recomputed assessment for one location."""
    if not request.scenario_id and not request.overrides:
        raise HTTPException(
            status_code=400, detail="Provide a scenario_id, overrides, or both."
        )
    try:
        result = simulation_service.run(
            scenario_id=request.scenario_id,
            overrides=request.overrides,
            merge=request.merge,
        )
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown scenario '{request.scenario_id}'"
        )

    payload: dict[str, Any] = {
        **result,
        "readouts": simulation_service.derived_readouts(result["overrides"]),
    }

    if request.location_id:
        try:
            region_service.get_location(request.location_id)
        except LocationNotFound:
            raise HTTPException(
                status_code=404, detail=f"Unknown location '{request.location_id}'"
            )
        payload["monitoring"] = await monitoring_service.get_monitoring(request.location_id)

        # Demonstration push for the ONE location being simulated. Overrides
        # apply everywhere, so this must not hang off the region-wide risk hook.
        # A messaging problem is reported in the payload and never fails the
        # simulator itself.
        payload["demo_alert"] = None
        if result.get("active"):
            try:
                context = alert_dispatch.location_context(request.location_id)
                if context is not None:
                    payload["demo_alert"] = await alert_dispatch.dispatch_simulation(
                        context,
                        payload["monitoring"]["risk"],
                        episode_id=result.get("episode_id"),
                    )
            except Exception as exc:  # noqa: BLE001
                payload["demo_alert"] = {"status": "FAILED", "detail": type(exc).__name__}

    return payload


@router.post("/simulation/reset")
async def reset(location_id: str | None = None) -> dict[str, Any]:
    """Return the platform to live data."""
    result = simulation_service.reset()
    payload: dict[str, Any] = {**result, "readouts": None}
    if location_id:
        try:
            region_service.get_location(location_id)
        except LocationNotFound:
            raise HTTPException(status_code=404, detail=f"Unknown location '{location_id}'")
        payload["monitoring"] = await monitoring_service.get_monitoring(location_id)
    return payload
