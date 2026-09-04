"""SOURCE 4 - Historical rainfall (Open-Meteo Archive / ERA5 reanalysis).

Two derived products, both computed from real reanalysis data:

**Rainfall anomaly percentile.** 60 mm of rain is unremarkable in one valley
and a record in another. This module pulls several years of ERA5 daily
precipitation for the exact location, restricts it to the same calendar window
as today, and reports where the current 24-hour total sits in that local
distribution. That converts a raw millimetre figure into a locally meaningful
signal.

**Antecedent Precipitation Index (API).** A standard hydrological proxy for how
wet the catchment already is:

    API = sum over i of k^i * P(t-i)

with a recession constant k = 0.9 over the previous 14 days. Saturated ground
converts far more of any new rainfall into surface runoff, which is why the
same storm can be harmless in May and dangerous in August.

No flood events are fabricated here. If a genuine labelled flood-event dataset
is supplied later, :func:`load_flood_events` will read it; until then it
returns an empty list and says so.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from app.config.logging_config import EV_FALLBACK, get_logger
from app.config.settings import settings
from app.services import data_cache
from app.services.data_cache import iso, utcnow
from app.services.http_client import UpstreamError, fetch_json

log = get_logger(__name__)

ARCHIVE_SOURCE_KEY = "open-meteo-archive"
ARCHIVE_SOURCE_LABEL = "Open-Meteo Archive API (ERA5 reanalysis)"
ARCHIVE_ATTRIBUTION = (
    "Historical rainfall from ECMWF ERA5 / ERA5-Land reanalysis, served by Open-Meteo.com"
)

CALENDAR_WINDOW_DAYS = 10   # +/- days around today's date used for the climatology
ARCHIVE_LAG_DAYS = 6        # ERA5 is not available for the last few days
API_RECESSION_K = 0.9
API_WINDOW_DAYS = 14


# --------------------------------------------------------------------------
# Rainfall climatology
# --------------------------------------------------------------------------
async def get_climatology(
    location_id: str, latitude: float, longitude: float
) -> dict[str, Any]:
    res = await get_climatology_batch([(location_id, latitude, longitude)])
    return res[location_id]


async def get_climatology_batch(
    points: Sequence[tuple[str, float, float]]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, float, float]] = []

    today = date.today()
    for pid, lat, lon in points:
        key = data_cache.make_key(
            "climatology", lat=lat, lon=lon, doy=today.timetuple().tm_yday,
            yrs=settings.climatology_years,
        )
        cached = data_cache.get(key)
        if cached is not None:
            out[pid] = _climatology_result(pid, cached.payload, "CACHED", cached.age_minutes)
        else:
            pending.append((pid, lat, lon))

    if not pending:
        return out

    end = today - timedelta(days=ARCHIVE_LAG_DAYS)
    start = end.replace(year=end.year - settings.climatology_years)

    for chunk_start in range(0, len(pending), 20):
        chunk = pending[chunk_start : chunk_start + 20]
        try:
            raw = await fetch_json(
                ARCHIVE_SOURCE_KEY,
                settings.open_meteo_archive_url,
                params={
                    "latitude": ",".join(f"{p[1]:.4f}" for p in chunk),
                    "longitude": ",".join(f"{p[2]:.4f}" for p in chunk),
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "daily": "precipitation_sum",
                    "timezone": "UTC",
                },
                timeout=settings.http_timeout_seconds + 25,
            )
            entries = raw if isinstance(raw, list) else [raw]
            for (pid, lat, lon), entry in zip(chunk, entries):
                payload = _build_climatology(entry, today)
                if payload is None:
                    out[pid] = _climatology_unavailable(pid, "empty archive series")
                    continue
                data_cache.put(
                    data_cache.make_key(
                        "climatology", lat=lat, lon=lon,
                        doy=today.timetuple().tm_yday, yrs=settings.climatology_years,
                    ),
                    payload, source="climatology", ttl_seconds=settings.cache_ttl_climatology,
                )
                out[pid] = _climatology_result(pid, payload, "LIVE", 0.0)

        except UpstreamError as exc:
            log.warning("%s ERA5 climatology unavailable: %s", EV_FALLBACK, str(exc)[:160])
            for pid, lat, lon in chunk:
                stale = data_cache.get(
                    data_cache.make_key(
                        "climatology", lat=lat, lon=lon,
                        doy=today.timetuple().tm_yday, yrs=settings.climatology_years,
                    ),
                    allow_expired=True,
                )
                if stale is not None:
                    out[pid] = _climatology_result(pid, stale.payload, "STALE_CACHE", stale.age_minutes)
                else:
                    out[pid] = _climatology_unavailable(pid, str(exc))
    return out


def _build_climatology(entry: dict[str, Any], today: date) -> dict[str, Any] | None:
    daily = (entry or {}).get("daily") or {}
    times = daily.get("time") or []
    values = daily.get("precipitation_sum") or []
    if not times or not values:
        return None

    target_doy = today.timetuple().tm_yday
    seasonal: list[float] = []
    annual: list[float] = []

    for t, v in zip(times, values):
        if v is None:
            continue
        try:
            d = date.fromisoformat(str(t)[:10])
        except ValueError:
            continue
        val = float(v)
        annual.append(val)
        delta = abs(d.timetuple().tm_yday - target_doy)
        delta = min(delta, 365 - delta)
        if delta <= CALENDAR_WINDOW_DAYS:
            seasonal.append(val)

    if len(seasonal) < 20:
        seasonal = annual
    if not seasonal:
        return None

    seasonal.sort()
    return {
        "sample_days": len(seasonal),
        "years": settings.climatology_years,
        "window_days": CALENDAR_WINDOW_DAYS,
        "distribution": [round(v, 2) for v in seasonal],
        "percentiles": {
            "p50": round(_pct(seasonal, 50), 2),
            "p75": round(_pct(seasonal, 75), 2),
            "p90": round(_pct(seasonal, 90), 2),
            "p95": round(_pct(seasonal, 95), 2),
            "p99": round(_pct(seasonal, 99), 2),
        },
        "max_observed_mm": round(max(seasonal), 2),
        "mean_mm": round(sum(seasonal) / len(seasonal), 2),
        "wet_day_fraction": round(sum(1 for v in seasonal if v >= 1.0) / len(seasonal), 3),
        "annual_sample_days": len(annual),
        "computed_for_doy": target_doy,
    }


def _pct(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def percentile_of(value: float | None, climatology: dict[str, Any] | None) -> float | None:
    """Percentile rank of ``value`` within the location's own rainfall history."""
    if value is None or not climatology:
        return None
    dist = climatology.get("distribution")
    if not dist:
        return None
    below = sum(1 for v in dist if v < value)
    equal = sum(1 for v in dist if abs(v - value) < 1e-9)
    return round(100.0 * (below + 0.5 * equal) / len(dist), 1)


def _climatology_result(
    pid: str, payload: dict[str, Any], freshness: str, age: float
) -> dict[str, Any]:
    slim = {k: v for k, v in payload.items() if k != "distribution"}
    return {
        "location_id": pid,
        "source": ARCHIVE_SOURCE_LABEL,
        "source_key": ARCHIVE_SOURCE_KEY,
        "attribution": ARCHIVE_ATTRIBUTION,
        "freshness": freshness,
        "age_minutes": round(age, 1),
        "observed_at": iso(utcnow()),
        "available": True,
        "method": (
            f"ERA5 daily precipitation for this exact location over the last "
            f"{payload.get('years')} years, restricted to a +/-{payload.get('window_days')} day "
            "calendar window around today."
        ),
        "_distribution": payload.get("distribution"),
        **slim,
    }


def _climatology_unavailable(pid: str, reason: str) -> dict[str, Any]:
    return {
        "location_id": pid,
        "source": ARCHIVE_SOURCE_LABEL,
        "source_key": ARCHIVE_SOURCE_KEY,
        "attribution": ARCHIVE_ATTRIBUTION,
        "freshness": "DEMO",
        "age_minutes": 0.0,
        "observed_at": iso(utcnow()),
        "available": False,
        "sample_days": 0,
        "percentiles": None,
        "max_observed_mm": None,
        "mean_mm": None,
        "method": "unavailable",
        "_distribution": None,
        "notes": [
            "ERA5 rainfall climatology unavailable - no anomaly percentile is being estimated.",
            f"Reason: {reason[:160]}",
        ],
    }


# --------------------------------------------------------------------------
# Antecedent Precipitation Index
# --------------------------------------------------------------------------
async def get_antecedent_index_batch(
    points: Sequence[tuple[str, float, float]]
) -> dict[str, dict[str, Any]]:
    """Recent daily rainfall and the derived API (soil-wetness proxy).

    Uses the forecast endpoint's ``past_days`` window rather than the archive,
    because ERA5 lags real time by several days and antecedent wetness must be
    current to be useful.
    """
    out: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, float, float]] = []

    for pid, lat, lon in points:
        key = data_cache.make_key("api-index", lat=lat, lon=lon, d=API_WINDOW_DAYS)
        cached = data_cache.get(key)
        if cached is not None:
            out[pid] = {**cached.payload, "location_id": pid, "freshness": "CACHED",
                        "age_minutes": round(cached.age_minutes, 1)}
        else:
            pending.append((pid, lat, lon))

    if not pending:
        return out

    for i in range(0, len(pending), settings.max_batch_points):
        chunk = pending[i : i + settings.max_batch_points]
        try:
            raw = await fetch_json(
                "open-meteo-forecast",
                settings.open_meteo_forecast_url,
                params={
                    "latitude": ",".join(f"{p[1]:.4f}" for p in chunk),
                    "longitude": ",".join(f"{p[2]:.4f}" for p in chunk),
                    "daily": "precipitation_sum",
                    "past_days": API_WINDOW_DAYS,
                    "forecast_days": 1,
                    "timezone": "auto",
                },
                timeout=settings.http_timeout_seconds + 10,
            )
            entries = raw if isinstance(raw, list) else [raw]
            for (pid, lat, lon), entry in zip(chunk, entries):
                payload = _build_api_index(entry)
                data_cache.put(
                    data_cache.make_key("api-index", lat=lat, lon=lon, d=API_WINDOW_DAYS),
                    payload, source="api-index", ttl_seconds=settings.cache_ttl_weather * 2,
                )
                out[pid] = {**payload, "location_id": pid, "freshness": "LIVE", "age_minutes": 0.0}

        except UpstreamError as exc:
            log.warning("%s antecedent index unavailable: %s", EV_FALLBACK, str(exc)[:160])
            for pid, lat, lon in chunk:
                stale = data_cache.get(
                    data_cache.make_key("api-index", lat=lat, lon=lon, d=API_WINDOW_DAYS),
                    allow_expired=True,
                )
                if stale is not None:
                    out[pid] = {**stale.payload, "location_id": pid,
                                "freshness": "STALE_CACHE",
                                "age_minutes": round(stale.age_minutes, 1)}
                else:
                    out[pid] = {
                        "location_id": pid, "freshness": "DEMO", "age_minutes": 0.0,
                        "api_mm": None, "recent_daily": [], "total_14d_mm": None,
                        "wet_days_14d": None,
                        "source": ARCHIVE_SOURCE_LABEL, "source_key": "open-meteo-forecast",
                        "attribution": ARCHIVE_ATTRIBUTION,
                        "method": "unavailable",
                        "notes": [f"Antecedent rainfall unavailable: {str(exc)[:160]}"],
                    }
    return out


def _build_api_index(entry: dict[str, Any]) -> dict[str, Any]:
    daily = (entry or {}).get("daily") or {}
    times = list(daily.get("time") or [])
    sums = list(daily.get("precipitation_sum") or [])

    pairs = [
        (str(t)[:10], float(v))
        for t, v in zip(times, sums)
        if v is not None
    ]
    # Drop today (partial) and keep the preceding window, most recent first.
    history = pairs[:-1][-API_WINDOW_DAYS:] if len(pairs) > 1 else pairs
    reverse = list(reversed(history))

    api_value = 0.0
    for i, (_, mm) in enumerate(reverse, start=1):
        api_value += (API_RECESSION_K ** i) * mm

    total = sum(mm for _, mm in history)
    return {
        "api_mm": round(api_value, 2),
        "recession_k": API_RECESSION_K,
        "window_days": API_WINDOW_DAYS,
        "total_14d_mm": round(total, 2),
        "wet_days_14d": sum(1 for _, mm in history if mm >= 1.0),
        "recent_daily": [{"date": d, "precipitation_mm": round(mm, 2)} for d, mm in history],
        "saturation_class": _saturation_class(api_value),
        "source": "Open-Meteo Forecast API (past-days window)",
        "source_key": "open-meteo-forecast",
        "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
        "method": (
            f"Antecedent Precipitation Index = sum of k^i * P(t-i) over the previous "
            f"{API_WINDOW_DAYS} days with recession constant k={API_RECESSION_K}."
        ),
        "notes": [],
    }


def _saturation_class(api_mm: float) -> str:
    if api_mm < 10:
        return "dry"
    if api_mm < 30:
        return "slightly moist"
    if api_mm < 60:
        return "moist"
    if api_mm < 100:
        return "wet"
    return "saturated"


# --------------------------------------------------------------------------
# Optional labelled flood-event dataset (architecture only)
# --------------------------------------------------------------------------
def load_flood_events(region_id: str | None = None) -> dict[str, Any]:
    """Read a user-supplied labelled flood-event dataset, if one exists.

    The prototype deliberately ships without one. No flood events are invented:
    if the file is absent this returns an empty list and explains what a valid
    dataset would need to contain.
    """
    path = settings.data_dir / "historical" / "flood_events.json"
    if not path.exists():
        return {
            "available": False,
            "events": [],
            "count": 0,
            "expected_path": str(path),
            "required_schema": {
                "event_id": "string",
                "date": "ISO-8601 date",
                "latitude": "float",
                "longitude": "float",
                "region_id": "string",
                "severity": "one of minor | moderate | major",
                "source": "citation for the record",
            },
            "notes": [
                "No labelled flood-event dataset is bundled with this prototype.",
                "Historical flood events are NOT fabricated. Supply a cited dataset at the "
                "expected path to enable supervised model training and validation.",
            ],
        }

    try:
        events = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"available": False, "events": [], "count": 0,
                "notes": [f"flood_events.json is not valid JSON: {exc}"]}

    if region_id:
        events = [e for e in events if e.get("region_id") == region_id]
    return {
        "available": True,
        "events": events,
        "count": len(events),
        "notes": ["User-supplied labelled dataset loaded from data/historical/flood_events.json"],
    }
