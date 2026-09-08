"""Decides whether a risk assessment should page anybody, and does it.

The risk engine is untouched. This module only reads the level it produced and
the level it produced last time, so the rule is a state transition rather than
"every time somebody loads the dashboard":

    LOW      -> MODERATE   no emergency alert
    MODERATE -> HIGH       HIGH alert
    HIGH     -> HIGH       nothing; the level did not change
    HIGH     -> EXTREME    EXTREME alert; the situation got worse
    EXTREME  -> EXTREME    nothing
    EXTREME  -> MODERATE   nothing; de-escalation is not an emergency

Two independent safeties sit in front of the send:

* ``flood_alert_test_mode`` (default ON) evaluates and records a transition but
  never sends a real push. The simulator can drive a location to EXTREME during
  a demonstration without paging anyone.
* Simulated risk NEVER sends a real emergency alert regardless of that setting.
  A scenario is not an emergency.

Nothing here reports success it did not have: a dispatch row records what FCM
accepted, and "accepted" is never described as "delivered".
"""
from __future__ import annotations

from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.db import get_conn, write_conn
from app.models.enums import RunMode
from app.services import device_registry, fcm_client
from app.services.data_cache import iso, parse_iso, utcnow

log = get_logger(__name__)

# Wording is deliberately about MODELLED RISK, never about a flood being
# certain. The 0-100 value is a relative risk score, not a probability.
TEMPLATES: dict[str, dict[str, str]] = {
    "HIGH": {
        "title": "⚠️ FloodSafe HIGH Risk Alert",
        "body": (
            "High flash-flood risk detected near {location}, {state}. "
            "Please monitor conditions and follow official local instructions."
        ),
    },
    "EXTREME": {
        "title": "🚨 FloodSafe EXTREME Risk Alert",
        "body": (
            "Extreme flash-flood risk detected near {location}, {state}. "
            "Move to safer areas and follow official local emergency instructions."
        ),
    },
}

TEST_TEMPLATE = {
    "title": "🧪 FloodSafe TEST ALERT",
    "body": (
        "This is a demonstration notification. No emergency is occurring. "
        "Sent to {location} devices to verify the flood-alert channel."
    ),
}


def _record(
    *,
    location_id: str,
    location_name: str | None,
    risk_level: str,
    risk_score: float | None,
    previous_level: str | None,
    mode: str,
    kind: str,
    targeted: int,
    result: fcm_client.SendResult,
) -> dict[str, Any]:
    row = {
        "location_id": location_id,
        "location_name": location_name,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "previous_level": previous_level,
        "mode": mode,
        "kind": kind,
        "targeted": targeted,
        "accepted": result.accepted,
        "rejected": result.rejected,
        "status": result.status,
        "detail": result.detail[:400] if result.detail else None,
        "sent_at": iso(utcnow()),
    }
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO alert_dispatches
               (location_id, location_name, risk_level, risk_score, previous_level,
                mode, kind, targeted, accepted, rejected, status, detail, sent_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(row[k] for k in (
                "location_id", "location_name", "risk_level", "risk_score",
                "previous_level", "mode", "kind", "targeted", "accepted",
                "rejected", "status", "detail", "sent_at")),
        )
    return row


def in_cooldown(location_id: str, risk_level: str) -> bool:
    """True if the same location and severity was alerted on recently.

    Guards against a score flapping across a class boundary
    (HIGH -> MODERATE -> HIGH) re-notifying every few minutes. An escalation to
    a DIFFERENT level is a different key and is never suppressed by this.
    """
    minutes = settings.alert_cooldown_minutes
    if minutes <= 0:
        return False
    with get_conn() as conn:
        row = conn.execute(
            """SELECT sent_at FROM alert_dispatches
               WHERE location_id=? AND risk_level=? AND kind='EMERGENCY'
                 AND status IN ('SENT','PARTIAL')
               ORDER BY sent_at DESC LIMIT 1""",
            (location_id, risk_level),
        ).fetchone()
    if not row:
        return False
    age_minutes = (utcnow() - parse_iso(row["sent_at"])).total_seconds() / 60.0
    return age_minutes < minutes


def recent_dispatches(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_dispatches ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def should_trigger(previous_level: str | None, new_level: str | None) -> bool:
    """A transition INTO a trigger level. Not a level, a change of level."""
    if not new_level or new_level.upper() not in settings.alert_trigger_level_set:
        return False
    return (previous_level or "").upper() != new_level.upper()


def _dashboard_link(location_id: str) -> str:
    return f"{settings.public_dashboard_url.rstrip('/')}/?location={location_id}"


async def dispatch_emergency(
    location: dict[str, Any],
    risk: dict[str, Any],
    previous_level: str | None,
    *,
    mode: RunMode = RunMode.LIVE,
) -> dict[str, Any] | None:
    """Send an emergency alert if - and only if - this really is a new one."""
    level = str(risk.get("risk_level") or "").upper()
    location_id = location.get("id") or ""
    location_name = location.get("name") or location_id

    if not should_trigger(previous_level, level):
        return None

    log.info(
        "[ALERT] %s risk transition detected (%s -> %s) at %s, score %s",
        level, previous_level or "none", level, location_name, risk.get("risk_score"),
    )

    # A scenario is not an emergency. This is checked before test mode so that
    # enabling real alerts can never also arm the simulator.
    if mode == RunMode.SIMULATION:
        log.info("[ALERT] suppressed: simulation mode is active")
        return _record(
            location_id=location_id, location_name=location_name, risk_level=level,
            risk_score=risk.get("risk_score"), previous_level=previous_level,
            mode=mode.value, kind="EMERGENCY", targeted=0,
            result=fcm_client.SendResult(
                status="SUPPRESSED_SIMULATION",
                detail="Simulated risk never sends a real emergency alert.",
            ),
        )

    if in_cooldown(location_id, level):
        log.info("[ALERT] suppressed: within the %d-minute cooldown", settings.alert_cooldown_minutes)
        return _record(
            location_id=location_id, location_name=location_name, risk_level=level,
            risk_score=risk.get("risk_score"), previous_level=previous_level,
            mode=mode.value, kind="EMERGENCY", targeted=0,
            result=fcm_client.SendResult(
                status="SUPPRESSED_COOLDOWN",
                detail=f"An alert for this location and severity was sent within "
                       f"{settings.alert_cooldown_minutes} minutes.",
            ),
        )

    targets = device_registry.find_targets(
        location_id=location_id,
        district=location.get("district"),
        state_id=location.get("state_id"),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
    )
    log.info("[ALERT] target devices: %d", len(targets))

    if settings.flood_alert_test_mode:
        log.info("[ALERT] suppressed: FLOOD_ALERT_TEST_MODE is on (no real push sent)")
        return _record(
            location_id=location_id, location_name=location_name, risk_level=level,
            risk_score=risk.get("risk_score"), previous_level=previous_level,
            mode=mode.value, kind="EMERGENCY", targeted=len(targets),
            result=fcm_client.SendResult(
                status="SUPPRESSED_TEST_MODE",
                detail="FLOOD_ALERT_TEST_MODE is enabled; the transition was "
                       "evaluated and recorded but no real push was sent.",
            ),
        )

    template = TEMPLATES.get(level, TEMPLATES["HIGH"])
    state = location.get("state_name") or location.get("district") or "your area"
    result = await fcm_client.send_to_tokens(
        [d["fcm_token"] for d in targets],
        title=template["title"],
        body=template["body"].format(location=location_name, state=state),
        severity=level,
        click_url=_dashboard_link(location_id),
        data={
            "kind": "EMERGENCY",
            "severity": level,
            "location_id": location_id,
            "location_name": location_name,
            "risk_score": str(risk.get("risk_score", "")),
            "tag": f"floodsafe-{location_id}-{level}",
        },
    )
    device_registry.deactivate_tokens(result.dead_tokens)
    return _record(
        location_id=location_id, location_name=location_name, risk_level=level,
        risk_score=risk.get("risk_score"), previous_level=previous_level,
        mode=mode.value, kind="EMERGENCY", targeted=len(targets), result=result,
    )


async def send_test_alert(*, location_id: str | None = None) -> dict[str, Any]:
    """A clearly-labelled test push. Always safe to run during a demonstration."""
    if location_id:
        targets = device_registry.find_targets(location_id=location_id)
        scope_name = location_id
    else:
        targets = device_registry.list_devices(active_only=True, include_token=True)
        scope_name = "all registered"

    result = await fcm_client.send_to_tokens(
        [d["fcm_token"] for d in targets],
        title=TEST_TEMPLATE["title"],
        body=TEST_TEMPLATE["body"].format(location=scope_name),
        severity="TEST",
        click_url=settings.public_dashboard_url,
        data={"kind": "TEST", "severity": "TEST", "tag": "floodsafe-test"},
    )
    device_registry.deactivate_tokens(result.dead_tokens)
    record = _record(
        location_id=location_id or "*", location_name=scope_name, risk_level="TEST",
        risk_score=None, previous_level=None, mode="TEST", kind="TEST",
        targeted=len(targets), result=result,
    )
    return {**record, "devices": [d["token_hint"] for d in targets]}


async def on_risk_assessed(
    location: dict[str, Any],
    risk: dict[str, Any],
    previous_level: str | None,
    *,
    mode: RunMode = RunMode.LIVE,
) -> None:
    """Hook called from the monitoring pipeline. Never raises.

    Notifications are an additional capability, not a dependency: if anything in
    here fails, the risk assessment it was called from must still be returned.
    """
    try:
        await dispatch_emergency(location, risk, previous_level, mode=mode)
    except Exception as exc:  # noqa: BLE001 - deliberate: must not break risk
        log.error("alert dispatch failed for %s: %s", location.get("id"), exc)
