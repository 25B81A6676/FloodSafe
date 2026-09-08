"""Registered phones and which of them an alert should reach.

Each physical phone holds its own FCM registration token and its own row here,
so five phones in three places are five independently addressable devices - not
one global subscriber. The token is the natural key: re-registering the same
browser updates its row instead of creating a duplicate.

Targeting is exact-match on the administrative hierarchy, widening from location
to district to state according to ``alert_targeting_scope``. That is enough for
the demonstration and is deliberately not a GIS engine; ``_within_radius`` is
present because the coordinates are already stored, and the resolver is shaped
so a radius scope slots in without changing any caller.
"""
from __future__ import annotations

import math
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.db import get_conn, write_conn
from app.services.data_cache import iso, utcnow
from app.services.fcm_client import mask_token

log = get_logger(__name__)

EARTH_RADIUS_M = 6_371_000.0


def _row_to_device(row: Any, *, include_token: bool = False) -> dict[str, Any]:
    """Public shape of a device. The FCM token is never included by default.

    A registration token is a bearer credential for pushing to that phone, so it
    does not belong in an API response or in the Command Centre - only a masked
    fragment, which is enough to tell two devices apart.
    """
    device = {
        "id": row["id"],
        "label": row["label"],
        "token_hint": mask_token(row["fcm_token"]),
        "location_id": row["location_id"],
        "location_name": row["location_name"],
        "district": row["district"],
        "state_id": row["state_id"],
        "state_name": row["state_name"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
    }
    if include_token:
        device["fcm_token"] = row["fcm_token"]
    return device


def register(
    *,
    fcm_token: str,
    location_id: str | None = None,
    location_name: str | None = None,
    district: str | None = None,
    state_id: str | None = None,
    state_name: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Create or update the row for one phone, keyed on its FCM token."""
    now = iso(utcnow())
    with write_conn() as conn:
        existing = conn.execute(
            "SELECT id, label FROM devices WHERE fcm_token = ?", (fcm_token,)
        ).fetchone()
        if existing is None:
            # Give an unnamed phone a stable, non-identifying label.
            count = conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"]
            resolved_label = label or f"Device {count + 1}"
            conn.execute(
                """INSERT INTO devices
                   (fcm_token, label, location_id, location_name, district, state_id,
                    state_name, latitude, longitude, active, created_at, updated_at,
                    last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                (fcm_token, resolved_label, location_id, location_name, district,
                 state_id, state_name, latitude, longitude, now, now, now),
            )
        else:
            conn.execute(
                """UPDATE devices SET
                     label=?, location_id=?, location_name=?, district=?, state_id=?,
                     state_name=?, latitude=?, longitude=?, active=1,
                     updated_at=?, last_seen_at=?
                   WHERE fcm_token=?""",
                (label or existing["label"], location_id, location_name, district,
                 state_id, state_name, latitude, longitude, now, now, fcm_token),
            )
        row = conn.execute("SELECT * FROM devices WHERE fcm_token=?", (fcm_token,)).fetchone()

    device = _row_to_device(row)
    log.info(
        "[DEVICE] registered %s (%s) at %s",
        device["label"], device["token_hint"], device["location_name"] or "unassigned",
    )
    return device


def list_devices(*, active_only: bool = False, include_token: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM devices"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY id"
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_device(r, include_token=include_token) for r in rows]


def count_active() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM devices WHERE active=1").fetchone()["n"]


def set_active(device_id: int, active: bool) -> dict[str, Any] | None:
    with write_conn() as conn:
        conn.execute(
            "UPDATE devices SET active=?, updated_at=? WHERE id=?",
            (1 if active else 0, iso(utcnow()), device_id),
        )
        row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    return _row_to_device(row) if row else None


def deactivate_tokens(tokens: list[str]) -> int:
    """Retire tokens FCM has told us are dead.

    Deactivated rather than deleted, so the Command Centre can still show that a
    phone was registered and has since become unreachable - silently dropping it
    would look like it had never registered.
    """
    if not tokens:
        return 0
    now = iso(utcnow())
    with write_conn() as conn:
        for token in tokens:
            conn.execute(
                "UPDATE devices SET active=0, updated_at=? WHERE fcm_token=?", (now, token)
            )
    log.info("[DEVICE] deactivated %d unreachable device(s)", len(tokens))
    return len(tokens)


def _within_radius(device: dict[str, Any], lat: float, lon: float, radius_m: float) -> bool:
    if device.get("latitude") is None or device.get("longitude") is None:
        return False
    p1, p2 = math.radians(float(device["latitude"])), math.radians(lat)
    dp = p2 - p1
    dl = math.radians(lon - float(device["longitude"]))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a)) <= radius_m


def find_targets(
    *,
    location_id: str | None,
    district: str | None = None,
    state_id: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    scope: str | None = None,
    radius_m: float = 25_000.0,
) -> list[dict[str, Any]]:
    """Active devices that should receive an alert for this place.

    An alert for Gaurikund must not reach a phone registered in Rishikesh, so
    the default scope is the exact location. Widening to district or state is a
    configuration change, not a code change.
    """
    scope = (scope or settings.alert_targeting_scope).lower()
    devices = list_devices(active_only=True, include_token=True)

    if scope == "radius" and latitude is not None and longitude is not None:
        return [d for d in devices if _within_radius(d, latitude, longitude, radius_m)]
    if scope == "state" and state_id:
        return [d for d in devices if d["state_id"] == state_id]
    if scope == "district" and district:
        return [d for d in devices if d["district"] == district]
    if location_id:
        return [d for d in devices if d["location_id"] == location_id]
    return []
