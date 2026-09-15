"""Flash-flood scenario simulator.

The simulator does not contain a second copy of the risk logic. It overrides
input features and then runs the *same* feature vector through the *same*
model as the live path. That is the point: a judge can watch the score move and
know the movement came from the real engine responding to changed inputs.

Simulated values are marked ``SIMULATION`` at the feature level, so they remain
visually distinct from measurements everywhere they surface.
"""
from __future__ import annotations

import json
import uuid
from functools import lru_cache
from typing import Any

from app.config.logging_config import EV_SIM, get_logger
from app.config.settings import settings
from app.database import repository
from app.services import risk_config
from app.services.feature_engineering import FEATURE_KEYS

log = get_logger(__name__)


# Every control maps to a real model feature - there are no cosmetic sliders.
CONTROLS: list[dict[str, Any]] = [
    {
        "key": "rainfall_intensity", "label": "Rainfall intensity",
        "group": "Rainfall", "unit": "mm/h", "min": 0, "max": 120, "step": 0.5,
        "help": "Current hourly rainfall rate. The IMD cloudburst threshold is 100 mm/h.",
    },
    {
        "key": "rainfall_3h", "label": "3-hour accumulation",
        "group": "Rainfall", "unit": "mm", "min": 0, "max": 150, "step": 1,
        "help": "Rain that has already fallen over the catchment response window.",
    },
    {
        "key": "rainfall_24h", "label": "24-hour accumulation",
        "group": "Rainfall", "unit": "mm", "min": 0, "max": 300, "step": 1,
        "help": "Daily total. IMD calls 115.6-204.4 mm 'very heavy rainfall'.",
    },
    {
        "key": "rainfall_forecast_24h", "label": "Forecast rainfall (next 24 h)",
        "group": "Rainfall", "unit": "mm", "min": 0, "max": 300, "step": 1,
        "help": "Expected additional rainfall. Drives lead time.",
    },
    {
        "key": "rainfall_trend", "label": "Rainfall trend",
        "group": "Rainfall", "unit": "mm/h per h", "min": -5, "max": 20, "step": 0.5,
        "help": "Positive means the storm is intensifying.",
    },
    {
        "key": "antecedent_precipitation_index", "label": "Soil saturation (API)",
        "group": "Catchment", "unit": "mm", "min": 0, "max": 160, "step": 1,
        "help": "Antecedent Precipitation Index. Above ~100 mm the catchment is saturated.",
    },
    {
        "key": "river_discharge_anomaly", "label": "River level (discharge ratio)",
        "group": "Catchment", "unit": "x mean", "min": 0.5, "max": 5.0, "step": 0.05,
        "help": "Discharge relative to the 30-day mean. 1.0 is normal, 3.0+ is critical.",
    },
    {
        "key": "slope", "label": "Terrain slope",
        "group": "Terrain", "unit": "deg", "min": 0, "max": 45, "step": 0.5,
        "help": "Steeper terrain concentrates runoff faster. Defaults to the real DEM value.",
    },
    {
        "key": "river_proximity", "label": "Distance to river",
        "group": "Terrain", "unit": "m", "min": 0, "max": 5000, "step": 25,
        "help": "Distance to the nearest mapped watercourse. Defaults to the real OSM value.",
    },
]

CONTROL_KEYS = {c["key"] for c in CONTROLS}


@lru_cache(maxsize=1)
def load_scenarios() -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    if not settings.scenarios_dir.exists():
        log.error("scenarios directory missing: %s", settings.scenarios_dir)
        return scenarios
    for path in sorted(settings.scenarios_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.error("scenario %s is invalid JSON: %s", path.name, exc)
            continue
        if "id" not in data:
            continue
        bad = [k for k in (data.get("overrides") or {}) if k not in FEATURE_KEYS]
        if bad:
            log.warning("scenario %s references unknown features: %s", data["id"], bad)
            for k in bad:
                data["overrides"].pop(k, None)
        scenarios.append(data)
        try:
            repository.upsert_scenario(data)
        except Exception as exc:  # noqa: BLE001 - persistence is a convenience here
            log.warning("could not persist scenario %s: %s", data["id"], exc)
    scenarios.sort(key=lambda s: s.get("order", 99))
    log.info("loaded %d simulation scenario(s)", len(scenarios))
    return scenarios


def reload_scenarios() -> None:
    load_scenarios.cache_clear()
    load_scenarios()


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    for s in load_scenarios():
        if s["id"] == scenario_id:
            return s
    return None


def list_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "short_name": s.get("short_name", s.get("name", s["id"])),
            "description": s.get("description"),
            "narrative": s.get("narrative"),
            "severity": s.get("severity"),
            "order": s.get("order", 99),
            "expected_risk_level": s.get("expected_risk_level"),
            "expected_score_range": s.get("expected_score_range"),
            "hydrology_label": s.get("hydrology_label"),
            "water_rise_rate_cm_per_hr": s.get("water_rise_rate_cm_per_hr"),
            "overrides": s.get("overrides", {}),
            "data_kind": s.get("data_kind", "SIMULATION"),
        }
        for s in load_scenarios()
    ]


def controls() -> list[dict[str, Any]]:
    """Slider definitions, annotated with the model metadata behind each one."""
    out = []
    for c in CONTROLS:
        fcfg = risk_config.feature_config(c["key"])
        out.append({
            **c,
            "model_weight": fcfg.get("weight"),
            "model_label": fcfg.get("label"),
            "rationale": fcfg.get("rationale"),
            "direction": fcfg.get("direction", "increasing"),
        })
    return out


def sanitize_overrides(raw: dict[str, Any] | None) -> tuple[dict[str, float], list[str]]:
    """Keep only known, numeric, in-range overrides. Report what was rejected."""
    clean: dict[str, float] = {}
    rejected: list[str] = []
    bounds = {c["key"]: (float(c["min"]), float(c["max"])) for c in CONTROLS}

    for key, value in (raw or {}).items():
        if key not in FEATURE_KEYS:
            rejected.append(f"{key}: not a model feature")
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            rejected.append(f"{key}: not numeric")
            continue
        if num != num:
            rejected.append(f"{key}: NaN")
            continue
        lo, hi = bounds.get(key, (float("-inf"), float("inf")))
        if num < lo or num > hi:
            num = min(max(num, lo), hi)
            rejected.append(f"{key}: clamped to [{lo:g}, {hi:g}]")
        clean[key] = num
    return clean, rejected


def run(
    *,
    scenario_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    merge: bool = False,
) -> dict[str, Any]:
    """Activate a scenario and/or manual overrides. Returns the new state."""
    base: dict[str, float] = {}
    scenario = None
    rejected: list[str] = []

    if merge:
        base = dict(repository.load_simulation_state().get("overrides") or {})

    if scenario_id:
        scenario = get_scenario(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        scenario_overrides, rej = sanitize_overrides(scenario.get("overrides"))
        rejected.extend(rej)
        base.update(scenario_overrides)

    if overrides:
        manual, rej = sanitize_overrides(overrides)
        rejected.extend(rej)
        base.update(manual)

    active = bool(base)
    # An episode begins when simulation turns on and ends at Exit Simulation
    # (which deletes the state row). Moving a slider while already simulating
    # keeps the same episode, which is what stops demo alerts repeating.
    previous = repository.load_simulation_state()
    episode_id = (
        (previous.get("episode_id") if previous.get("active") else None) or uuid.uuid4().hex[:12]
    ) if active else None
    repository.save_simulation_state(
        base, scenario_id=scenario_id, active=active, episode_id=episode_id
    )
    log.info("%s activated scenario=%s overrides=%s", EV_SIM, scenario_id, sorted(base))

    return {
        "active": active,
        "episode_id": episode_id,
        "scenario_id": scenario_id,
        "scenario": scenario,
        "overrides": base,
        "rejected": rejected,
    }


def reset() -> dict[str, Any]:
    repository.clear_simulation_state()
    log.info("%s reset - returning to live data", EV_SIM)
    return {"active": False, "scenario_id": None, "overrides": {}, "rejected": []}


def current_state() -> dict[str, Any]:
    state = repository.load_simulation_state()
    scenario = get_scenario(state["scenario_id"]) if state.get("scenario_id") else None
    return {**state, "scenario": scenario}


def derived_readouts(overrides: dict[str, Any]) -> dict[str, Any]:
    """Scenario-level display values that are not themselves model features.

    ``water_rise_rate`` is a presentation figure derived from the simulated
    discharge ratio and rainfall trend. It is a scenario attribute, not a
    measurement, and is labelled as such in the UI.
    """
    ratio = float(overrides.get("river_discharge_anomaly", 1.0) or 1.0)
    trend = float(overrides.get("rainfall_trend", 0.0) or 0.0)
    rise = max(0.0, (ratio - 1.0) * 22.0 + max(0.0, trend) * 1.4)
    if ratio >= 3.0:
        label = "Critical"
    elif ratio >= 2.0:
        label = "High"
    elif ratio >= 1.35:
        label = "Rising"
    elif ratio >= 0.8:
        label = "Normal"
    else:
        label = "Low"
    return {
        "water_rise_rate_cm_per_hr": round(rise, 1),
        "river_level_label": label,
        "is_scenario_attribute": True,
        "note": "Derived scenario readout, not a measured water level.",
    }
