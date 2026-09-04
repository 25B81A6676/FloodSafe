"""Resilient async HTTP access to external data sources.

Every outbound call goes through :func:`fetch_json`, which applies a timeout,
bounded retries with backoff, and records per-source health so the dashboard
can show an honest connectivity indicator.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.config.logging_config import EV_API_FAIL, EV_FETCH, get_logger
from app.config.settings import network_disabled, settings
from app.models.enums import SourceStatus
from app.services.data_cache import utcnow

log = get_logger(__name__)


class UpstreamError(RuntimeError):
    """Raised when an external source cannot be reached or returns bad data."""


@dataclass
class SourceHealth:
    name: str
    status: SourceStatus = SourceStatus.UNKNOWN
    last_success: datetime | None = None
    last_failure: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    total_calls: int = 0
    total_failures: int = 0
    last_latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_failure": self.last_failure.isoformat() if self.last_failure else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "last_latency_ms": (
                round(self.last_latency_ms, 1) if self.last_latency_ms is not None else None
            ),
        }


_health: dict[str, SourceHealth] = {}
_client: httpx.AsyncClient | None = None

#: Every source the platform can use. Listed explicitly so the UI can show a
#: source as UNKNOWN before its first call, rather than hiding it entirely.
KNOWN_SOURCES: dict[str, str] = {
    "open-meteo-forecast": "Open-Meteo Forecast API (weather)",
    "open-meteo-archive": "Open-Meteo Archive / ERA5 (rainfall climatology)",
    "open-meteo-flood": "Open-Meteo Flood API / GloFAS (river discharge)",
    "open-meteo-elevation": "Open-Meteo Elevation (Copernicus DEM)",
    "open-topo-data": "OpenTopoData SRTM (elevation fallback)",
    "open-elevation": "Open-Elevation (elevation fallback)",
    "overpass": "Overpass API / OpenStreetMap (rivers, infrastructure)",
}


def health_for(source: str) -> SourceHealth:
    if source not in _health:
        _health[source] = SourceHealth(name=source)
    return _health[source]


def all_health(*, include_unused: bool = True) -> list[dict[str, Any]]:
    """Health of every source, labelled. Unused sources report UNKNOWN."""
    rows = {h.name: h.to_dict() for h in _health.values()}
    if include_unused:
        for name in KNOWN_SOURCES:
            rows.setdefault(name, SourceHealth(name=name).to_dict())
    for name, row in rows.items():
        row["label"] = KNOWN_SOURCES.get(name, name)
    return sorted(rows.values(), key=lambda r: r["name"])


def _mark_success(source: str, latency_ms: float) -> None:
    h = health_for(source)
    h.status = SourceStatus.OK
    h.last_success = utcnow()
    h.consecutive_failures = 0
    h.total_calls += 1
    h.last_latency_ms = latency_ms
    h.last_error = None


def _mark_failure(source: str, err: str) -> None:
    h = health_for(source)
    h.last_failure = utcnow()
    h.consecutive_failures += 1
    h.total_calls += 1
    h.total_failures += 1
    h.last_error = err[:300]
    h.status = SourceStatus.DOWN if h.consecutive_failures >= 2 else SourceStatus.DEGRADED


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def fetch_json(
    source: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> Any:
    """Fetch and parse JSON, or raise :class:`UpstreamError`.

    Never raises anything else, so callers can implement a clean fallback.
    """
    if network_disabled():
        _mark_failure(source, "network disabled by FLOODSAFE_DISABLE_NETWORK")
        raise UpstreamError(f"{source}: outbound network disabled")

    attempts = (retries if retries is not None else settings.http_max_retries) + 1
    tmo = timeout or settings.http_timeout_seconds
    client = await get_client()
    last_err: str = "unknown error"

    for attempt in range(1, attempts + 1):
        started = asyncio.get_event_loop().time()
        try:
            if method.upper() == "POST":
                resp = await client.post(url, data=data, timeout=tmo)
            else:
                resp = await client.get(url, params=params, timeout=tmo)
            latency = (asyncio.get_event_loop().time() - started) * 1000.0

            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}: {resp.text[:160]}"
                raise UpstreamError(last_err)

            payload = resp.json()
            _mark_success(source, latency)
            log.info("%s %s ok in %.0f ms", EV_FETCH, source, latency)
            return payload

        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                backoff = settings.http_backoff_seconds * attempt
                log.warning(
                    "%s %s attempt %d/%d failed (%s), retrying in %.1fs",
                    EV_API_FAIL, source, attempt, attempts, last_err[:120], backoff,
                )
                await asyncio.sleep(backoff)
            else:
                _mark_failure(source, last_err)
                log.error("%s %s exhausted %d attempts: %s", EV_API_FAIL, source, attempts, last_err[:200])

    raise UpstreamError(f"{source}: {last_err}")
