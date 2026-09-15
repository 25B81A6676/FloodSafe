"""Decides whether a risk assessment should page anybody, and does it.

The risk engine is untouched. This module only reads the level the engine
produced. There are three separate notification paths, deliberately kept apart:

1. REAL alerts - measured risk transitions into HIGH/EXTREME
   (``dispatch_emergency``). Governed by ``flood_alert_test_mode`` and a
   wall-clock cooldown. Simulated risk can never reach this path.

       LOW      -> MODERATE   no alert
       MODERATE -> HIGH       HIGH alert
       HIGH     -> HIGH       nothing; the level did not change
       HIGH     -> EXTREME    EXTREME alert; the situation got worse
       EXTREME  -> EXTREME    nothing
       EXTREME  -> MODERATE   nothing; de-escalation is not an emergency

2. SIMULATION demo alerts - the simulator drives the SELECTED location into
   HIGH/EXTREME (``dispatch_simulation``). A real FCM push, labelled in its
   title and body as an SIH demonstration. De-duplicated per simulation
   episode, so the demonstration can be repeated after Exit Simulation.

3. TEST alerts - the "Send test alert" button (``send_test_alert``).

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

# The demonstration label sits in BOTH the title and the body, so it is visible
# whether a phone shows the collapsed or the expanded notification.
SIMULATION_TEMPLATES: dict[str, dict[str, str]] = {
    "HIGH": {
        "title": "⚠️ FLOODSAFE HIGH SIMULATION ALERT",
        "body": (
            "HIGH flash-flood risk simulated at {location}, {state}.\n"
            "Risk Score: {score}\n"
            "🧪 SIH DEMONSTRATION — NOT A REAL EMERGENCY"
        ),
    },
    "EXTREME": {
        "title": "🚨 FLOODSAFE EXTREME SIMULATION ALERT",
        "body": (
            "EXTREME flash-flood risk simulated at {location}, {state}.\n"
            "Risk Score: {score}\n"
            "🧪 SIH DEMONSTRATION — NOT A REAL EMERGENCY"
        ),
    },
}

TEST_TEMPLATE = {
    "title": "🧪 FLOODSAFE TEST ALERT",
    "body": (
        "This is a notification delivery test. No emergency is occurring. "
        "Sent to {location} devices to verify the flood-alert channel."
    ),
}

# Statuses that mean a simulation alert for that level is settled for the
# episode. FAILED is deliberately absent: a network blip should not cost the
# demonstration its alert, so the next slider move retries it.
_SIMULATION_SETTLED = ("SENDING", "SENT", "PARTIAL", "NO_TARGETS", "NOT_CONFIGURED", "DISABLED")


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
    episode_id: str | None = None,
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
        "episode_id": episode_id,
    }
    with write_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO alert_dispatches
               (location_id, location_name, risk_level, risk_score, previous_level,
                mode, kind, targeted, accepted, rejected, status, detail, sent_at,
                episode_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(row[k] for k in (
                "location_id", "location_name", "risk_level", "risk_score",
                "previous_level", "mode", "kind", "targeted", "accepted",
                "rejected", "status", "detail", "sent_at", "episode_id")),
        )
        row["id"] = cursor.lastrowid
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


def click_path(location: dict[str, Any]) -> str:
    """A path back to this place, relative to whatever origin the phone used.

    Relative on purpose: a phone reaches FloodSafe through an HTTPS tunnel or a
    deployment, never through the laptop's localhost, so an absolute URL built on
    the server would open a page the phone cannot reach. The service worker joins
    this to its own origin.
    """
    from urllib.parse import urlencode

    params = {
        "state": location.get("state_id") or "",
        "district": location.get("district_id") or "",
        "location": location.get("id") or "",
    }
    return "/?" + urlencode(params)


def location_context(location_id: str) -> dict[str, Any] | None:
    """Everything a notification needs about a place, from the region registry."""
    from app.services import region_service

    try:
        loc = region_service.get_location(location_id)
    except region_service.LocationNotFound:
        return None
    ctx: dict[str, Any] = {
        **loc.to_dict(),
        "state_id": None,
        "state_name": None,
        "district_id": None,
    }
    try:
        region = region_service.get_region(loc.region_id)
    except region_service.RegionNotFound:
        return ctx
    scope = region.get("scope")
    if scope == "district":
        ctx["district_id"] = region["id"]
        ctx["state_id"] = region.get("state_id")
        ctx["state_name"] = region.get("state_name")
    elif scope == "national":
        ctx["state_name"] = "India"
    else:
        # A state scope, or a curated pilot region (which is itself a state).
        ctx["state_id"] = region.get("state_id") or region["id"]
        ctx["state_name"] = region.get("state_name") or region.get("name")
    return ctx


async def dispatch_emergency(
    location: dict[str, Any],
    risk: dict[str, Any],
    previous_level: str | None,
    *,
    mode: RunMode = RunMode.LIVE,
) -> dict[str, Any] | None:
    """Send a REAL emergency alert if - and only if - this really is a new one."""
    level = str(risk.get("risk_level") or "").upper()
    location_id = location.get("id") or ""
    location_name = location.get("name") or location_id

    if not should_trigger(previous_level, level):
        return None

    log.info(
        "[ALERT] %s risk transition detected (%s -> %s) at %s, score %s",
        level, previous_level or "none", level, location_name, risk.get("risk_score"),
    )

    # Simulated risk never reaches the REAL alert path. Simulation has its own
    # clearly-labelled demonstration path, dispatch_simulation().
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
            "location_id": location_id,
            "location_name": location_name,
            "risk_score": str(risk.get("risk_score", "")),
            "tag": f"floodsafe-{location_id}-{level}",
            "click_path": click_path(location),
            "sent_at": iso(utcnow()),
        },
    )
    device_registry.deactivate_tokens(result.dead_tokens)
    return _record(
        location_id=location_id, location_name=location_name, risk_level=level,
        risk_score=risk.get("risk_score"), previous_level=previous_level,
        mode=mode.value, kind="EMERGENCY", targeted=len(targets), result=result,
    )


# --------------------------------------------------------------------------
# Simulation demonstration alerts
# --------------------------------------------------------------------------
def _reserve_simulation_alert(
    *, episode_id: str, location: dict[str, Any], level: str, score: float | None,
) -> int | None:
    """Atomically decide whether this level still needs an alert this episode.

    Returns the id of a placeholder row claiming the send, or None if the alert
    is already settled. The check and the claim happen inside one write lock,
    so two slider moves arriving together cannot both send the same alert.

        HIGH    is sent once, and not at all if EXTREME was already sent
        EXTREME is sent once
    """
    location_id = location.get("id") or ""
    with write_conn() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT risk_level FROM alert_dispatches
                WHERE kind='SIMULATION' AND episode_id=? AND location_id=?
                  AND status IN ({",".join("?" * len(_SIMULATION_SETTLED))})""",
            (episode_id, location_id, *_SIMULATION_SETTLED),
        ).fetchall()
        settled = {r["risk_level"] for r in rows}
        if level in settled or (level == "HIGH" and "EXTREME" in settled):
            return None
        cursor = conn.execute(
            """INSERT INTO alert_dispatches
               (location_id, location_name, risk_level, risk_score, previous_level,
                mode, kind, targeted, accepted, rejected, status, detail, sent_at,
                episode_id)
               VALUES (?,?,?,?,NULL,'SIMULATION','SIMULATION',0,0,0,'SENDING',NULL,?,?)""",
            (location_id, location.get("name"), level, score, iso(utcnow()), episode_id),
        )
        return cursor.lastrowid


def _settle_simulation_alert(row_id: int, *, targeted: int, result: fcm_client.SendResult) -> None:
    with write_conn() as conn:
        conn.execute(
            """UPDATE alert_dispatches
               SET targeted=?, accepted=?, rejected=?, status=?, detail=?, sent_at=?
               WHERE id=?""",
            (targeted, result.accepted, result.rejected, result.status,
             result.detail[:400] if result.detail else None, iso(utcnow()), row_id),
        )


async def dispatch_simulation(
    location: dict[str, Any],
    risk: dict[str, Any],
    *,
    episode_id: str | None,
) -> dict[str, Any] | None:
    """Push a clearly-labelled DEMONSTRATION alert for the simulated location.

    Only ever called for the one location the simulator is driving. Simulator
    overrides apply to every location, so a region-wide refresh during a
    simulation pushes every place to the same level; calling this from that
    generic path would page every registered phone rather than the ones at the
    place being demonstrated.
    """
    level = str(risk.get("risk_level") or "").upper()
    if level not in {"HIGH", "EXTREME"} or not episode_id:
        return None
    if not settings.simulation_alerts_enabled:
        log.info("[SIMULATION] %s reached, demo alerts disabled by configuration", level)
        return None

    score = risk.get("risk_score")
    location_id = location.get("id") or ""
    location_name = location.get("name") or location_id

    row_id = _reserve_simulation_alert(
        episode_id=episode_id, location=location, level=level, score=score
    )
    if row_id is None:
        return None  # already alerted for this level in this episode

    log.info("[SIMULATION] %s risk reached", level)
    log.info("[ALERT] Location: %s", location_name)
    log.info("[ALERT] Risk score: %s", score)

    targets = device_registry.find_targets(
        location_id=location_id,
        district=location.get("district"),
        state_id=location.get("state_id"),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
    )
    log.info("[ALERT] Target devices: %d", len(targets))

    if not targets:
        result = fcm_client.SendResult(
            status="NO_TARGETS",
            detail=f"No active devices are registered at {location_name}.",
        )
    else:
        template = SIMULATION_TEMPLATES[level]
        state = location.get("state_name") or location.get("district") or "India"
        log.info("[FCM] Sending simulation notification")
        try:
            result = await fcm_client.send_to_tokens(
                [d["fcm_token"] for d in targets],
                title=template["title"],
                body=template["body"].format(location=location_name, state=state, score=score),
                severity=level,
                click_url=_dashboard_link(location_id),
                data={
                    "kind": "SIMULATION",
                    "location_id": location_id,
                    "location_name": location_name,
                    "state_name": str(state),
                    "risk_score": str(score if score is not None else ""),
                    "tag": f"floodsafe-sim-{location_id}-{level}",
                    "click_path": click_path(location),
                    "sent_at": iso(utcnow()),
                },
            )
        except Exception as exc:  # noqa: BLE001 - must never break the simulator
            log.error("[FCM] simulation dispatch failed: %s", type(exc).__name__)
            result = fcm_client.SendResult(status="FAILED", detail=type(exc).__name__)
        device_registry.deactivate_tokens(result.dead_tokens)
        log.info("[FCM] Accepted: %d", result.accepted)
        log.info("[FCM] Invalid tokens: %d", len(result.dead_tokens))

    _settle_simulation_alert(row_id, targeted=len(targets), result=result)
    return {
        "id": row_id,
        "kind": "SIMULATION",
        "episode_id": episode_id,
        "location_id": location_id,
        "location_name": location_name,
        "risk_level": level,
        "risk_score": score,
        "targeted": len(targets),
        "accepted": result.accepted,
        "rejected": result.rejected,
        "invalid_tokens": len(result.dead_tokens),
        "status": result.status,
        "detail": result.detail or None,
    }


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
        data={"kind": "TEST", "tag": "floodsafe-test", "click_path": "/", "sent_at": iso(utcnow())},
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

    Only REAL measured risk is handled here. During a simulation every location
    in a region is assessed with the same overrides, so this hook would see all
    of them jump to EXTREME at once; demonstration alerts are instead sent by
    the simulator endpoint for the single location being simulated.
    """
    if mode == RunMode.SIMULATION:
        return
    try:
        await dispatch_emergency(location, risk, previous_level, mode=mode)
    except Exception as exc:  # noqa: BLE001 - deliberate: must not break risk
        log.error("alert dispatch failed for %s: %s", location.get("id"), exc)
