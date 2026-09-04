"""Alert generation.

Alerts are advisory decision-support messages for disaster-management staff.
The prototype does not issue evacuation orders and every message says so.
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.models.enums import RiskLevel
from app.services.data_cache import iso, utcnow

ADVISORY_FOOTER = (
    "Decision support only. FloodSafe is a prototype and does not issue official "
    "warnings or evacuation orders. Verify against IMD and SDMA bulletins."
)

_TEMPLATES: dict[str, dict[str, Any]] = {
    RiskLevel.SAFE.value: {
        "severity": "INFO",
        "headline": "Conditions normal",
        "message": (
            "Current environmental conditions indicate low flash-flood susceptibility. "
            "Routine monitoring is sufficient."
        ),
        "actions": [
            "Continue routine monitoring of rainfall and river levels.",
        ],
    },
    RiskLevel.LOW.value: {
        "severity": "INFO",
        "headline": "Low flash-flood susceptibility",
        "message": (
            "Conditions are largely benign. Background susceptibility reflects fixed "
            "terrain and channel proximity rather than active weather."
        ),
        "actions": [
            "Continue routine monitoring.",
            "No operational action required at this time.",
        ],
    },
    RiskLevel.MODERATE.value: {
        "severity": "ADVISORY",
        "headline": "Monitor rainfall and local conditions",
        "message": (
            "Rainfall and catchment conditions are elevated. Flash-flood risk is not "
            "imminent but the catchment has reduced capacity to absorb further rainfall."
        ),
        "actions": [
            "Increase observation frequency for rainfall and river level.",
            "Review readiness of local response resources.",
            "Advise caution near stream channels and low-lying crossings.",
        ],
    },
    RiskLevel.HIGH.value: {
        "severity": "WARNING",
        "headline": "Flash-flood risk is elevated",
        "message": (
            "Flash-flood risk is elevated. Authorities should prepare for potential "
            "impacts along the drainage network and at valley-floor crossings."
        ),
        "actions": [
            "Alert district disaster-management staff and local response teams.",
            "Restrict access to riverbanks, causeways and low bridges.",
            "Pre-position response resources on the safe side of likely cut-off points.",
            "Check communication with upstream and downstream settlements.",
        ],
    },
    RiskLevel.EXTREME.value: {
        "severity": "CRITICAL",
        "headline": "EXTREME flash-flood risk",
        "message": (
            "EXTREME FLASH-FLOOD RISK. Immediate attention from disaster-management "
            "authorities is recommended. In steep terrain, warning times at this level "
            "are measured in minutes, not hours."
        ),
        "actions": [
            "Escalate immediately to the district emergency operations centre.",
            "Clear riverbanks, campsites, parking areas and pilgrimage routes near channels.",
            "Close vulnerable bridges and valley-floor roads.",
            "Activate contingency plans for settlements identified as high or extreme risk.",
            "Coordinate with IMD and the State Disaster Management Authority for official warnings.",
        ],
    },
}


def build_alert(risk: dict[str, Any], location: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn a risk assessment into an actionable advisory."""
    level = risk.get("risk_level", RiskLevel.SAFE.value)
    tpl = _TEMPLATES.get(level, _TEMPLATES[RiskLevel.SAFE.value])
    loc_name = (location or {}).get("name") or risk.get("location_id", "the selected location")

    drivers = [
        f"{c['icon']} {c['factor']} - {c['display_value']}"
        for c in (risk.get("contributors") or [])
        if c.get("impact") in {"HIGH", "MEDIUM"}
    ][:5]

    simulated = risk.get("mode") == "SIMULATION"
    ident = hashlib.sha1(
        f"{risk.get('location_id')}|{level}|{risk.get('timestamp')}".encode()
    ).hexdigest()[:12]

    return {
        "id": f"alert_{ident}",
        "location_id": risk.get("location_id"),
        "location_name": loc_name,
        "risk_level": level,
        "risk_score": risk.get("risk_score"),
        "severity": tpl["severity"],
        "headline": tpl["headline"],
        "message": tpl["message"].replace("the selected location", loc_name),
        "actions": tpl["actions"],
        "drivers": drivers,
        "confidence": risk.get("confidence"),
        "issued_at": risk.get("timestamp") or iso(utcnow()),
        "mode": risk.get("mode", "LIVE"),
        "is_simulation": simulated,
        "simulation_notice": (
            "SIMULATED SCENARIO - this advisory reflects simulator inputs, not observations."
            if simulated else None
        ),
        "advisory_footer": ADVISORY_FOOTER,
    }


def severity_rank(severity: str) -> int:
    return {"INFO": 0, "ADVISORY": 1, "WARNING": 2, "CRITICAL": 3}.get(severity, 0)


def summarize(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """Region-level alert roll-up for the authority dashboard."""
    actionable = [a for a in alerts if a.get("severity") in {"ADVISORY", "WARNING", "CRITICAL"}]
    by_severity: dict[str, int] = {}
    for a in alerts:
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1

    def rank(a: dict[str, Any]) -> tuple[int, float]:
        return (severity_rank(a["severity"]), a.get("risk_score") or 0)

    ordered = sorted(alerts, key=rank, reverse=True)
    ordered_actionable = sorted(actionable, key=rank, reverse=True)
    top = ordered_actionable[0] if ordered_actionable else (ordered[0] if ordered else None)

    return {
        "total": len(alerts),
        "actionable": len(actionable),
        "by_severity": by_severity,
        "highest_severity": top["severity"] if top else "INFO",
        "top_alert": top,
        # Every alert, most severe first. Callers that only want operational
        # advisories use `actionable_alerts`.
        "alerts": ordered,
        "actionable_alerts": ordered_actionable,
        "advisory_footer": ADVISORY_FOOTER,
        "generated_at": iso(utcnow()),
    }
