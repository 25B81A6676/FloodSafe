"""Firebase Cloud Messaging transport (HTTP v1).

Deliberately a thin REST client rather than the ``firebase-admin`` SDK: this
project already speaks to every upstream over ``httpx`` with its own retry and
fallback conventions, and firebase-admin would pull in grpcio and the Firestore
client to send what is ultimately one HTTPS POST. The only thing genuinely
needed from Google's libraries is an OAuth2 access token for the service
account, which ``google-auth`` provides.

Nothing here fabricates success. If FCM is not configured, or the network fails,
or a token is rejected, the caller is told exactly that - "accepted" means FCM
accepted the request, never that a phone displayed anything.

SECURITY: the service-account private key is read from a file path or an
environment variable and is never logged, never returned by an endpoint, and
never sent to the frontend. Registration tokens are masked in every log line.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config.logging_config import get_logger
from app.config.settings import network_disabled, settings

log = get_logger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"

# FCM error codes that mean "this registration token is dead". The device row is
# deactivated rather than deleted, so the Command Centre can still show that a
# phone was registered and has since become unreachable.
DEAD_TOKEN_CODES = {"UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND", "SENDER_ID_MISMATCH"}

_credentials_lock = threading.Lock()
_credentials: Any = None


def mask_token(token: str | None) -> str:
    """A token fragment safe to log or show in the UI."""
    if not token:
        return "—"
    return f"{token[:6]}…{token[-4:]}" if len(token) > 14 else "…"


@dataclass(slots=True)
class SendResult:
    """Outcome of one dispatch. ``accepted`` is FCM's acceptance, not delivery."""

    status: str  # SENT | PARTIAL | FAILED | NOT_CONFIGURED | DISABLED
    accepted: int = 0
    rejected: int = 0
    dead_tokens: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"SENT", "PARTIAL"}


def _service_account_info() -> dict[str, Any] | None:
    if settings.fcm_service_account_json.strip():
        try:
            return json.loads(settings.fcm_service_account_json)
        except json.JSONDecodeError as exc:
            log.error("FCM_SERVICE_ACCOUNT_JSON is not valid JSON: %s", exc)
            return None
    path_value = settings.fcm_service_account_file.strip()
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = settings.project_root / path
    if not path.exists():
        log.error("FCM service account file not found: %s", path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("FCM service account file is not valid JSON: %s", exc)
        return None


def project_id() -> str:
    if settings.fcm_project_id.strip():
        return settings.fcm_project_id.strip()
    info = _service_account_info()
    return (info or {}).get("project_id", "")


def configured() -> bool:
    return bool(project_id()) and _service_account_info() is not None


def config_status() -> dict[str, Any]:
    """Non-sensitive description of the messaging configuration."""
    info = _service_account_info()
    return {
        "configured": configured(),
        "project_id": project_id() or None,
        # Identifies WHICH service account without revealing the key.
        "service_account": (info or {}).get("client_email"),
        "credential_source": (
            "env" if settings.fcm_service_account_json.strip()
            else "file" if settings.fcm_service_account_file.strip()
            else None
        ),
        "test_mode": settings.flood_alert_test_mode,
        "cooldown_minutes": settings.alert_cooldown_minutes,
        "targeting_scope": settings.alert_targeting_scope,
        "trigger_levels": sorted(settings.alert_trigger_level_set),
    }


def _access_token() -> str | None:
    """Cached OAuth2 token for the service account, refreshed when stale."""
    global _credentials
    info = _service_account_info()
    if info is None:
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError:
        log.error("google-auth is not installed; push notifications are unavailable")
        return None

    with _credentials_lock:
        if _credentials is None:
            try:
                _credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=[FCM_SCOPE]
                )
            except Exception as exc:  # noqa: BLE001 - bad key material
                log.error("FCM service account is unusable: %s", type(exc).__name__)
                return None
        try:
            if not _credentials.valid:
                _credentials.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - network or clock problems
            log.error("could not obtain an FCM access token: %s", type(exc).__name__)
            return None
        return _credentials.token


def reset_credentials() -> None:
    """Drop the cached credential (used by tests and after a config change)."""
    global _credentials
    with _credentials_lock:
        _credentials = None


def build_message(
    token: str,
    *,
    title: str,
    body: str,
    data: dict[str, str],
    severity: str,
    click_url: str,
) -> dict[str, Any]:
    """One FCM HTTP v1 message.

    Sound and vibration are requested through the platform-specific blocks. The
    web push channel has no custom-sound field at all, so the browser plays its
    own notification sound and the page adds the FloodSafe tone itself when it
    is open - see the frontend notification service.
    """
    return {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {**data, "click_url": click_url},
            "android": {
                "priority": "high",
                "notification": {
                    "sound": "default",
                    "notification_priority": "PRIORITY_MAX" if severity == "EXTREME" else "PRIORITY_HIGH",
                    "default_vibrate_timings": True,
                },
            },
            "apns": {
                "headers": {"apns-priority": "10"},
                "payload": {"aps": {"sound": "default"}},
            },
            "webpush": {
                "headers": {"Urgency": "high"},
                "notification": {
                    "title": title,
                    "body": body,
                    "icon": "/favicon.svg",
                    "badge": "/favicon.svg",
                    "tag": data.get("tag", "floodsafe-alert"),
                    "renotify": True,
                    "requireInteraction": severity == "EXTREME",
                    "vibrate": [300, 150, 300, 150, 600] if severity == "EXTREME" else [250, 150, 250],
                },
                "fcm_options": {"link": click_url},
            },
        }
    }


async def send_to_tokens(
    tokens: list[str],
    *,
    title: str,
    body: str,
    data: dict[str, str],
    severity: str,
    click_url: str,
) -> SendResult:
    """Send one notification to many devices.

    FCM HTTP v1 has no multicast endpoint, so this is one request per device.
    That is entirely reasonable at the scale this serves (a handful of phones)
    and it means one bad token cannot stop the others from being sent.
    """
    if not tokens:
        return SendResult(status="SENT", detail="no devices targeted")
    # Configuration is checked FIRST: a missing Firebase project is true
    # regardless of whether the network happens to be reachable, and it is the
    # more useful thing to report back.
    if not configured():
        return SendResult(
            status="NOT_CONFIGURED",
            detail="Firebase Cloud Messaging is not configured on this server",
        )
    if network_disabled():
        return SendResult(status="DISABLED", detail="outbound network is disabled")

    access_token = _access_token()
    if not access_token:
        return SendResult(status="FAILED", detail="could not obtain an FCM access token")

    url = FCM_ENDPOINT.format(project=project_id())
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    accepted, rejected, dead, errors = 0, 0, [], []
    try:
        async with httpx.AsyncClient(timeout=settings.fcm_timeout_seconds) as client:
            for token in tokens:
                payload = build_message(
                    token, title=title, body=body, data=data,
                    severity=severity, click_url=click_url,
                )
                try:
                    response = await client.post(url, headers=headers, json=payload)
                except httpx.HTTPError as exc:
                    # A per-device network failure must not abort the rest.
                    rejected += 1
                    errors.append(f"{mask_token(token)}: {type(exc).__name__}")
                    continue

                if response.status_code == 200:
                    accepted += 1
                    continue

                rejected += 1
                code = ""
                try:
                    error = response.json().get("error", {})
                    code = (error.get("details") or [{}])[0].get("errorCode") or error.get("status", "")
                except (ValueError, AttributeError, IndexError):
                    code = f"HTTP {response.status_code}"
                errors.append(f"{mask_token(token)}: {code}")
                if code in DEAD_TOKEN_CODES or response.status_code == 404:
                    dead.append(token)
    except Exception as exc:  # noqa: BLE001 - never let messaging break a risk response
        log.error("FCM dispatch failed: %s", type(exc).__name__)
        return SendResult(
            status="FAILED", accepted=accepted, rejected=len(tokens) - accepted,
            detail=f"{type(exc).__name__}",
        )

    status = "SENT" if rejected == 0 else ("PARTIAL" if accepted else "FAILED")
    log.info(
        "%s FCM accepted=%d rejected=%d dead=%d", "[FCM]", accepted, rejected, len(dead)
    )
    return SendResult(
        status=status, accepted=accepted, rejected=rejected,
        dead_tokens=dead, detail="; ".join(errors[:5]),
    )
