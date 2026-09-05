"""SOURCE 3a - Hydrology (Open-Meteo Flood API / Copernicus GloFAS).

GloFAS is the Global Flood Awareness System operated by the Copernicus
Emergency Management Service. It provides modelled daily river discharge on a
roughly 5 km grid. This is an *independent* signal: it is not derived from the
same feed as the rainfall shown elsewhere in the dashboard, so agreement
between them is meaningful corroboration.

Important modelling detail
--------------------------
GloFAS routes flow through discrete grid cells. The cell containing a
settlement centroid is frequently a hillslope cell carrying almost no flow,
while the actual river channel sits in an adjacent cell. Measured directly at
Rishikesh the centre cell reports ~0.9 m3/s while the neighbouring cell holding
the Ganga channel reports ~1400 m3/s.

We therefore sample a small stencil of neighbouring cells and adopt the cell
with the highest mean discharge as the representative river cell, recording
which offset was used. Doing otherwise would report a hillside as if it were
the river.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

from app.config.logging_config import EV_DEMO, EV_FALLBACK, get_logger
from app.config.settings import settings
from app.services import data_cache
from app.services.data_cache import iso, utcnow
from app.services.http_client import UpstreamError, fetch_json
from app.services.region_service import haversine_m
from app.services.validation import ValidationReport, clean_number

log = get_logger(__name__)

SOURCE_KEY = "open-meteo-flood"
SOURCE_LABEL = "Open-Meteo Flood API (Copernicus GloFAS v4)"
SOURCE_ATTRIBUTION = (
    "River discharge from the Copernicus Emergency Management Service "
    "Global Flood Awareness System (GloFAS), served by Open-Meteo.com"
)

PAST_DAYS = 30
FORECAST_DAYS = 7

# Offsets in degrees. GloFAS is ~0.05 deg, so +/-0.05 reaches the adjacent cell.
_D = settings.glofas_stencil_offset_deg
STENCIL_FULL: tuple[tuple[float, float], ...] = (
    (0.0, 0.0), (_D, 0.0), (-_D, 0.0), (0.0, _D),
    (0.0, -_D), (_D, _D), (-_D, -_D),
)
STENCIL_COMPACT: tuple[tuple[float, float], ...] = (
    (0.0, 0.0), (_D, 0.0), (-_D, 0.0), (0.0, _D), (0.0, -_D),
)


async def get_hydrology(
    location_id: str, latitude: float, longitude: float, *, force_refresh: bool = False
) -> dict[str, Any]:
    res = await get_hydrology_batch(
        [(location_id, latitude, longitude)], force_refresh=force_refresh
    )
    return res[location_id]


async def get_hydrology_batch(
    points: Sequence[tuple[str, float, float]],
    *,
    compact: bool = False,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    stencil = STENCIL_COMPACT if compact else STENCIL_FULL
    out: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, float, float]] = []

    for pid, lat, lon in points:
        key = data_cache.make_key("glofas", lat=lat, lon=lon, n=len(stencil))
        cached = None if force_refresh else data_cache.get(key)
        if cached is not None:
            out[pid] = _build(pid, lat, lon, cached.payload,
                              freshness="CACHED", age_minutes=cached.age_minutes)
        else:
            pending.append((pid, lat, lon))

    if not pending:
        return out

    flat: list[tuple[float, float]] = []
    spans: list[tuple[str, float, float, int, int]] = []
    for pid, lat, lon in pending:
        start = len(flat)
        flat.extend((round(lat + d[0], 4), round(lon + d[1], 4)) for d in stencil)
        spans.append((pid, lat, lon, start, len(flat)))

    entries: list[dict[str, Any] | None] = [None] * len(flat)
    failure: str | None = None

    for i in range(0, len(flat), settings.max_batch_points):
        chunk = flat[i : i + settings.max_batch_points]
        try:
            raw = await fetch_json(
                SOURCE_KEY,
                settings.open_meteo_flood_url,
                params={
                    "latitude": ",".join(f"{p[0]:.4f}" for p in chunk),
                    "longitude": ",".join(f"{p[1]:.4f}" for p in chunk),
                    "daily": "river_discharge",
                    "past_days": PAST_DAYS,
                    "forecast_days": FORECAST_DAYS,
                },
                timeout=settings.http_timeout_seconds + 15,
            )
            block = raw if isinstance(raw, list) else [raw]
            for j, e in enumerate(block[: len(chunk)]):
                entries[i + j] = e
        except UpstreamError as exc:
            failure = str(exc)
            log.warning("%s GloFAS chunk %d failed: %s", EV_FALLBACK, i, exc)

    for pid, lat, lon, start, end in spans:
        block = entries[start:end]
        payload = _select_representative(block, stencil, lat, lon)
        if payload is not None:
            data_cache.put(
                data_cache.make_key("glofas", lat=lat, lon=lon, n=len(stencil)),
                payload, source="glofas", ttl_seconds=settings.cache_ttl_hydrology,
            )
            out[pid] = _build(pid, lat, lon, payload, freshness="LIVE", age_minutes=0.0)
        else:
            stale = data_cache.get(
                data_cache.make_key("glofas", lat=lat, lon=lon, n=len(stencil)),
                allow_expired=True,
            )
            if stale is not None:
                out[pid] = _build(pid, lat, lon, stale.payload, freshness="STALE_CACHE",
                                  age_minutes=stale.age_minutes,
                                  note="Live GloFAS unavailable; showing last successful fetch.")
            else:
                out[pid] = _demo_hydrology(pid, lat, lon, failure or "no discharge data")
    return out


def _select_representative(
    entries: Sequence[dict[str, Any] | None],
    stencil: Sequence[tuple[float, float]],
    lat: float,
    lon: float,
) -> dict[str, Any] | None:
    """Pick the stencil cell with the highest mean discharge as the channel."""
    best: dict[str, Any] | None = None
    best_mean = -1.0
    centre_mean: float | None = None

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        daily = entry.get("daily") or {}
        series = daily.get("river_discharge") or []
        values = [v for v in series if isinstance(v, (int, float))]
        if not values:
            continue
        mean = sum(values) / len(values)
        if idx == 0:
            centre_mean = mean
        if mean > best_mean:
            best_mean = mean
            best = {
                "times": daily.get("time") or [],
                "discharge": series,
                "cell_lat": entry.get("latitude"),
                "cell_lon": entry.get("longitude"),
                "offset_index": idx,
                "offset": list(stencil[idx]) if idx < len(stencil) else [0.0, 0.0],
                "mean_window": round(mean, 3),
                "requested_lat": lat,
                "requested_lon": lon,
            }

    if best is None:
        return None
    best["centre_mean"] = round(centre_mean, 3) if centre_mean is not None else None
    best["used_neighbour"] = best["offset_index"] != 0
    if best.get("cell_lat") is not None and best.get("cell_lon") is not None:
        best["cell_distance_m"] = round(
            haversine_m(lat, lon, float(best["cell_lat"]), float(best["cell_lon"])), 0
        )
    return best


def _today_index(times: Sequence[str]) -> int:
    today = date.today().isoformat()
    for i, t in enumerate(times):
        if str(t)[:10] == today:
            return i
    return min(PAST_DAYS, max(0, len(times) - 1))


def _build(
    location_id: str, lat: float, lon: float, payload: dict[str, Any],
    *, freshness: str, age_minutes: float, note: str | None = None,
) -> dict[str, Any]:
    report = ValidationReport()
    times: list[str] = list(payload.get("times") or [])
    series: list[float | None] = list(payload.get("discharge") or [])
    idx = _today_index(times)

    current = clean_number(
        series[idx] if idx < len(series) else None, "discharge_m3s", report, default=0.0
    ) or 0.0

    past_vals = [v for v in series[:idx] if isinstance(v, (int, float))]
    mean_30d = round(sum(past_vals) / len(past_vals), 3) if past_vals else None
    max_30d = round(max(past_vals), 3) if past_vals else None

    ratio = None
    if mean_30d and mean_30d > 1e-6:
        ratio = round(current / mean_30d, 3)
    ratio = clean_number(ratio, "discharge_ratio", report, default=1.0)

    fut = [v for v in series[idx + 1 :] if isinstance(v, (int, float))]
    forecast_peak = round(max(fut), 3) if fut else None
    forecast_change = (
        round(((forecast_peak - current) / current) * 100, 1)
        if forecast_peak is not None and current > 1e-6 else None
    )

    if ratio is None:
        status, trend = "UNKNOWN", "UNKNOWN"
    elif ratio >= 3.0:
        status = "CRITICAL"
    elif ratio >= 2.0:
        status = "HIGH"
    elif ratio >= 1.35:
        status = "RISING"
    elif ratio >= 0.8:
        status = "NORMAL"
    else:
        status = "LOW"

    if forecast_change is None:
        trend = "UNKNOWN"
    elif forecast_change > 15:
        trend = "RISING"
    elif forecast_change < -15:
        trend = "FALLING"
    else:
        trend = "STEADY"

    chart = [
        {"date": times[i], "discharge": series[i], "kind": "observed" if i <= idx else "forecast"}
        for i in range(len(times))
        if i < len(series)
    ]

    return {
        "location_id": location_id,
        "latitude": lat,
        "longitude": lon,
        "source": SOURCE_LABEL,
        "source_key": SOURCE_KEY,
        "attribution": SOURCE_ATTRIBUTION,
        "freshness": freshness,
        "age_minutes": round(age_minutes, 1),
        "observed_at": iso(utcnow()),
        "valid_date": times[idx] if idx < len(times) else None,
        "discharge_m3s": round(current, 3),
        "mean_30d_m3s": mean_30d,
        "max_30d_m3s": max_30d,
        "discharge_ratio": ratio,
        "forecast_peak_7d_m3s": forecast_peak,
        "forecast_change_pct": forecast_change,
        "river_status": status,
        "river_trend": trend,
        "representative_cell": {
            "latitude": payload.get("cell_lat"),
            "longitude": payload.get("cell_lon"),
            "distance_m": payload.get("cell_distance_m"),
            "used_neighbour": payload.get("used_neighbour", False),
            "centre_cell_mean_m3s": payload.get("centre_mean"),
        },
        "series": chart,
        "method": (
            "Representative channel cell selected as the highest-mean-discharge cell "
            f"within a {len(STENCIL_FULL)}-cell GloFAS stencil. Anomaly ratio is today's "
            "discharge divided by the mean of the preceding 30 days at that same cell."
        ),
        "validation": report.to_dict(),
        "notes": [note] if note else [],
    }


def _demo_hydrology(pid: str, lat: float, lon: float, reason: str) -> dict[str, Any]:
    log.warning("%s hydrology fallback for %s (%s)", EV_DEMO, pid, reason[:120])
    return {
        "location_id": pid, "latitude": lat, "longitude": lon,
        "source": "Synthetic demonstration data", "source_key": SOURCE_KEY,
        "attribution": SOURCE_ATTRIBUTION,
        "freshness": "DEMO", "age_minutes": 0.0, "observed_at": iso(utcnow()),
        "valid_date": date.today().isoformat(),
        "discharge_m3s": None, "mean_30d_m3s": None, "max_30d_m3s": None,
        "discharge_ratio": 1.0, "forecast_peak_7d_m3s": None, "forecast_change_pct": None,
        "river_status": "UNKNOWN", "river_trend": "UNKNOWN",
        "representative_cell": {
            "latitude": None, "longitude": None, "distance_m": None,
            "used_neighbour": False, "centre_cell_mean_m3s": None,
        },
        "series": [],
        "method": "unavailable",
        "validation": {"ok": True, "errors": [], "warnings": [], "repaired": [], "dropped_fields": []},
        "notes": [
            "GloFAS river discharge unavailable - no measurement is being shown.",
            "Discharge anomaly defaults to a neutral 1.0 so it neither raises nor lowers the risk score.",
            f"Reason: {reason[:160]}",
        ],
    }
