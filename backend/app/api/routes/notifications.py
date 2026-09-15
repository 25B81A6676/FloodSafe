"""Flood-alert device registration and dispatch endpoints.

No endpoint here ever returns an FCM registration token. A token is a bearer
credential for pushing to that phone, so the API exposes only a masked hint,
which is enough to tell two devices apart in the Command Centre.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.config.settings import settings
from app.services import alert_dispatch, device_registry, fcm_client

router = APIRouter(tags=["notifications"])


class DeviceRegistration(BaseModel):
    """A phone asking to receive flood alerts for one place."""

    fcm_token: str = Field(min_length=20, max_length=4096)
    location_id: str | None = Field(default=None, max_length=200)
    location_name: str | None = Field(default=None, max_length=200)
    district: str | None = Field(default=None, max_length=200)
    state_id: str | None = Field(default=None, max_length=200)
    state_name: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    label: str | None = Field(default=None, max_length=60)

    @field_validator("fcm_token")
    @classmethod
    def _token_shape(cls, value: str) -> str:
        token = value.strip()
        if not token or " " in token:
            raise ValueError("fcm_token must be a single non-empty registration token")
        return token


@router.post("/notifications/register")
async def register_device(body: DeviceRegistration) -> dict[str, Any]:
    """Register or refresh one phone. Re-registering updates, never duplicates."""
    fields = body.model_dump()
    # The server's own registry is the authority on where a location is. The page
    # sends what it has on screen, which for a curated pilot region was a state
    # NAME in the state_id slot; resolving from location_id keeps district- and
    # state-scope targeting correct whatever the client sends.
    if body.location_id:
        context = alert_dispatch.location_context(body.location_id)
        if context is not None:
            fields["location_name"] = context.get("name") or fields["location_name"]
            fields["district"] = context.get("district") or fields["district"]
            fields["state_id"] = context.get("state_id") or fields["state_id"]
            fields["state_name"] = context.get("state_name") or fields["state_name"]
            if fields["latitude"] is None:
                fields["latitude"] = context.get("latitude")
                fields["longitude"] = context.get("longitude")
    device = device_registry.register(**fields)
    return {
        "registered": True,
        "device": device,
        "active_devices": device_registry.count_active(),
        "notifications_configured": fcm_client.configured(),
        "notice": (
            None if fcm_client.configured() else
            "This device is registered, but Firebase Cloud Messaging is not "
            "configured on the server, so no push can actually be delivered yet."
        ),
    }


@router.get("/notifications/status")
async def notification_status() -> dict[str, Any]:
    """Configuration and device state. Never includes a registration token."""
    devices = device_registry.list_devices()
    return {
        **fcm_client.config_status(),
        "active_devices": sum(1 for d in devices if d["active"]),
        "total_devices": len(devices),
        "devices": devices,
        "recent_dispatches": alert_dispatch.recent_dispatches(limit=10),
        "note": (
            "'accepted' means Firebase accepted the send request. It is not a "
            "confirmation that a phone displayed the notification."
        ),
    }


@router.post("/notifications/test")
async def send_test(
    location_id: str | None = Query(
        default=None,
        description="Restrict the test to devices registered at this location; "
                    "omit to reach every active device.",
    ),
) -> dict[str, Any]:
    """Send a clearly-labelled TEST notification. Safe during a demonstration."""
    result = await alert_dispatch.send_test_alert(location_id=location_id)
    # Both of these mean nothing was sent. Returning 200 would let the UI imply
    # a test alert went out when it did not.
    if result["status"] == "NOT_CONFIGURED":
        raise HTTPException(
            status_code=503,
            detail="Firebase Cloud Messaging is not configured on this server. "
                   "See docs/NOTIFICATIONS.md.",
        )
    if result["status"] == "DISABLED":
        raise HTTPException(
            status_code=503,
            detail="Outbound network is disabled on this server, so no push was sent.",
        )
    return result


@router.post("/notifications/devices/{device_id}/enable")
async def enable_device(device_id: int) -> dict[str, Any]:
    device = device_registry.set_active(device_id, True)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Unknown device {device_id}")
    return {"device": device}


@router.post("/notifications/devices/{device_id}/disable")
async def disable_device(device_id: int) -> dict[str, Any]:
    device = device_registry.set_active(device_id, False)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Unknown device {device_id}")
    return {"device": device}


@router.get("/notifications/config")
async def public_config() -> dict[str, Any]:
    """Firebase *client* configuration for the browser.

    These values are public by design - they identify the Firebase project to
    the SDK and are visible in any web client. The service-account private key
    is server-side only and is never served here.
    """
    return {
        "configured": bool(settings.firebase_web_api_key and settings.firebase_vapid_key),
        "firebase": {
            "apiKey": settings.firebase_web_api_key or None,
            "authDomain": settings.firebase_auth_domain or None,
            "projectId": settings.firebase_web_project_id or fcm_client.project_id() or None,
            "messagingSenderId": settings.firebase_messaging_sender_id or None,
            "appId": settings.firebase_app_id or None,
        },
        "vapidKey": settings.firebase_vapid_key or None,
        "serverReady": fcm_client.configured(),
    }
