"""Durable storage for registered phones, so a restart does not lose them.

Everything else this application stores is a cache: if it is lost it is fetched
again. A device registration cannot be. The token is minted on the phone, and
only the phone can produce another one - so losing the table means walking to
every phone and registering it again.

That is exactly what happens on a serverless host, where the SQLite file lives
in /tmp and is discarded whenever the instance is recycled. This module mirrors
the devices table into Cloud Firestore, in the SAME Firebase project that
already sends the notifications, using the service-account credentials that are
already configured. No second account, no second secret.

Design notes:

* SQLite stays the working store. Everything reads and writes it as before;
  this is a write-through mirror plus a one-shot restore on a cold start. That
  keeps the query surface, the tests and the offline path untouched.
* Every call here is best-effort. Firestore being unreachable, unconfigured, or
  not yet created must never break registration or stop an alert going out; it
  degrades to exactly the previous behaviour, and says so in the log.
* Off unless ``device_store`` is set to "firestore", so local development and
  the test suite never touch the network.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any

import httpx

from app.config.logging_config import get_logger
from app.config.settings import network_disabled, settings
from app.services import fcm_client

log = get_logger(__name__)

# Firestore needs its own OAuth scope; the messaging token cannot be reused.
SCOPE = "https://www.googleapis.com/auth/datastore"
COLLECTION = "floodsafe_devices"
TIMEOUT = 6.0

# Columns mirrored, with the Firestore value type each maps to.
TEXT_FIELDS = (
    "fcm_token", "label", "location_id", "location_name", "district",
    "state_id", "state_name", "created_at", "updated_at", "last_seen_at",
)
NUMBER_FIELDS = ("latitude", "longitude")

_credentials: Any = None
_lock = threading.Lock()
_warned = False


def enabled() -> bool:
    """True when mirroring is switched on and usable."""
    if settings.device_store.strip().lower() != "firestore":
        return False
    if network_disabled():
        return False
    return fcm_client.configured()


def _documents_url() -> str:
    return (
        f"https://firestore.googleapis.com/v1/projects/{fcm_client.project_id()}"
        f"/databases/(default)/documents/{COLLECTION}"
    )


def _access_token() -> str | None:
    """OAuth2 token for Firestore, cached and refreshed like the FCM one."""
    global _credentials
    info = fcm_client.service_account_info()
    if info is None:
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        log.error("google-auth import failed (%s); device mirroring unavailable", exc)
        return None

    with _lock:
        if _credentials is None:
            try:
                _credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=[SCOPE]
                )
            except Exception as exc:  # noqa: BLE001 - bad key material
                log.error("Firestore credentials unusable: %s", type(exc).__name__)
                return None
        try:
            if not _credentials.valid:
                _credentials.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - network or clock problems
            log.error("could not obtain a Firestore access token: %s", type(exc).__name__)
            return None
        return _credentials.token


def _doc_id(fcm_token: str) -> str:
    """Stable id per phone. Hashed because the token is a bearer credential and
    a document path turns up in URLs, logs and error messages."""
    return hashlib.sha256(fcm_token.encode()).hexdigest()[:40]


def _encode(device: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in TEXT_FIELDS:
        value = device.get(key)
        fields[key] = {"nullValue": None} if value is None else {"stringValue": str(value)}
    for key in NUMBER_FIELDS:
        value = device.get(key)
        fields[key] = {"nullValue": None} if value is None else {"doubleValue": float(value)}
    fields["active"] = {"booleanValue": bool(device.get("active", True))}
    return {"fields": fields}


def _decode(document: dict[str, Any]) -> dict[str, Any] | None:
    fields = document.get("fields") or {}

    def value(key: str) -> Any:
        holder = fields.get(key) or {}
        if "stringValue" in holder:
            return holder["stringValue"]
        if "doubleValue" in holder:
            return float(holder["doubleValue"])
        if "integerValue" in holder:
            return float(holder["integerValue"])
        if "booleanValue" in holder:
            return bool(holder["booleanValue"])
        return None

    token = value("fcm_token")
    if not token:
        return None  # a document without a token cannot be pushed to
    device: dict[str, Any] = {key: value(key) for key in TEXT_FIELDS + NUMBER_FIELDS}
    device["active"] = bool(value("active"))
    return device


def _warn_once(message: str, *args: Any) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning(message, *args)


def save(device: dict[str, Any]) -> bool:
    """Mirror one device. Returns whether it was stored."""
    if not enabled() or not device.get("fcm_token"):
        return False
    token = _access_token()
    if token is None:
        return False
    url = f"{_documents_url()}/{_doc_id(str(device['fcm_token']))}"
    try:
        response = httpx.patch(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=_encode(device),
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        _warn_once("device mirroring unavailable (%s); registrations are not durable",
                   type(exc).__name__)
        return False
    if response.status_code >= 400:
        _warn_once(
            "Firestore rejected a device write (HTTP %s). If the database has not been "
            "created in the Firebase console, registrations will not survive a restart.",
            response.status_code,
        )
        return False
    return True


def load_all() -> list[dict[str, Any]]:
    """Every mirrored device. Empty on any failure - never raises."""
    if not enabled():
        return []
    token = _access_token()
    if token is None:
        return []
    devices: list[dict[str, Any]] = []
    page: str | None = None
    try:
        while True:
            params: dict[str, Any] = {"pageSize": 300}
            if page:
                params["pageToken"] = page
            response = httpx.get(
                _documents_url(),
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=TIMEOUT,
            )
            if response.status_code == 404:
                # No collection yet: nothing has ever registered. Not an error.
                return []
            if response.status_code >= 400:
                _warn_once("Firestore rejected a device read (HTTP %s)", response.status_code)
                return []
            payload = response.json()
            for document in payload.get("documents", []):
                decoded = _decode(document)
                if decoded:
                    devices.append(decoded)
            page = payload.get("nextPageToken")
            if not page:
                break
    except httpx.HTTPError as exc:
        _warn_once("device mirroring unavailable (%s); starting with an empty device list",
                   type(exc).__name__)
        return []
    return devices
