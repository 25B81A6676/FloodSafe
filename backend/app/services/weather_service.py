"""SOURCE 1 - Weather (Open-Meteo Forecast API).

Open-Meteo is free for non-commercial use and requires no API key. It serves
blended national weather-model output; for this region that is primarily
ECMWF IFS and DWD ICON.

Fallback chain: LIVE -> CACHE (within TTL) -> STALE CACHE -> DEMO.
Demo values are deterministic per location and always labelled as DEMO so they
can never be mistaken for observations.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from app.config.logging_config import EV_DEMO, EV_FALLBACK, get_logger
from app.config.settings import settings
from app.services import data_cache
from app.services.data_cache import iso, utcnow
from app.services.http_client import UpstreamError, fetch_json
from app.services.validation import (
    ValidationReport,
    check_monotonic_accumulation,
    clean_number,
    dedupe_series,
)

log = get_logger(__name__)

SOURCE_KEY = "open-meteo-forecast"
SOURCE_LABEL = "Open-Meteo Forecast API"
SOURCE_ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

PAST_HOURS = 48
FORECAST_HOURS = 48

_CURRENT_VARS = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,rain,"
    "surface_pressure,pressure_msl,wind_speed_10m,wind_direction_10m,weather_code,"
    "cloud_cover"
)
_HOURLY_VARS = "precipitation,precipitation_probability,rain,temperature_2m,relative_humidity_2m"


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
async def get_weather(
    location_id: str, latitude: float, longitude: float, *, force_refresh: bool = False
) -> dict[str, Any]:
    """Normalised weather observation for one point, with honest freshness."""
    key = data_cache.make_key(SOURCE_KEY, lat=latitude, lon=longitude)

    if not force_refresh:
        cached = data_cache.get(key)
        if cached is not None:
            return _normalise(
                cached.payload, location_id, latitude, longitude,
                freshness="CACHED", age_minutes=cached.age_minutes,
            )

    try:
        raw = await fetch_json(
            SOURCE_KEY,
            settings.open_meteo_forecast_url,
            params=_params(latitude, longitude),
        )
        data_cache.put(key, raw, source=SOURCE_KEY, ttl_seconds=settings.cache_ttl_weather)
        return _normalise(raw, location_id, latitude, longitude, freshness="LIVE", age_minutes=0.0)

    except UpstreamError as exc:
        stale = data_cache.get(key, allow_expired=True)
        if stale is not None:
            log.warning(
                "%s weather API unavailable for %s, serving cache aged %.0f min",
                EV_FALLBACK, location_id, stale.age_minutes,
            )
            return _normalise(
                stale.payload, location_id, latitude, longitude,
                freshness="STALE_CACHE", age_minutes=stale.age_minutes,
                note=f"Live weather unavailable ({exc}); showing last successful fetch.",
            )
        log.warning("%s no weather cache for %s, using demo data", EV_DEMO, location_id)
        return _demo_weather(location_id, latitude, longitude, reason=str(exc))


async def get_weather_batch(
    points: Sequence[tuple[str, float, float]], *, force_refresh: bool = False
) -> dict[str, dict[str, Any]]:
    """Weather for many points using Open-Meteo multi-coordinate requests.

    One HTTP call covers up to ``settings.max_batch_points`` locations, which is
    what makes a real per-cell risk grid affordable instead of interpolating a
    single point across the whole region.
    """
    results: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, float, float]] = []

    for pid, lat, lon in points:
        key = data_cache.make_key(SOURCE_KEY, lat=lat, lon=lon)
        cached = None if force_refresh else data_cache.get(key)
        if cached is not None:
            results[pid] = _normalise(
                cached.payload, pid, lat, lon,
                freshness="CACHED", age_minutes=cached.age_minutes,
            )
        else:
            pending.append((pid, lat, lon))

    for chunk in _chunks(pending, settings.max_batch_points):
        if not chunk:
            continue
        try:
            raw = await fetch_json(
                SOURCE_KEY,
                settings.open_meteo_forecast_url,
                params=_params(
                    ",".join(f"{p[1]:.4f}" for p in chunk),
                    ",".join(f"{p[2]:.4f}" for p in chunk),
                ),
                timeout=settings.http_timeout_seconds + 15,
            )
            entries = raw if isinstance(raw, list) else [raw]
            for (pid, lat, lon), entry in zip(chunk, entries):
                data_cache.put(
                    data_cache.make_key(SOURCE_KEY, lat=lat, lon=lon),
                    entry, source=SOURCE_KEY, ttl_seconds=settings.cache_ttl_weather,
                )
                results[pid] = _normalise(entry, pid, lat, lon, freshness="LIVE", age_minutes=0.0)

        except UpstreamError as exc:
            log.warning("%s batch weather failed (%s), falling back per point", EV_FALLBACK, exc)
            for pid, lat, lon in chunk:
                stale = data_cache.get(
                    data_cache.make_key(SOURCE_KEY, lat=lat, lon=lon), allow_expired=True
                )
                if stale is not None:
                    results[pid] = _normalise(
                        stale.payload, pid, lat, lon,
                        freshness="STALE_CACHE", age_minutes=stale.age_minutes,
                        note="Live weather unavailable; showing last successful fetch.",
                    )
                else:
                    results[pid] = _demo_weather(pid, lat, lon, reason=str(exc))

    return results


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
def _params(latitude: Any, longitude: Any) -> dict[str, Any]:
    return {
        "latitude": latitude,
        "longitude": longitude,
        "current": _CURRENT_VARS,
        "hourly": _HOURLY_VARS,
        "past_hours": PAST_HOURS,
        "forecast_hours": FORECAST_HOURS,
        "timezone": "auto",
    }


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _parse_hour(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _sum_window(values: list[float | None], end_idx: int, hours: int) -> float | None:
    """Sum the ``hours`` values ending at (and including) ``end_idx``."""
    start = max(0, end_idx - hours + 1)
    window = [v for v in values[start : end_idx + 1] if v is not None]
    if not window:
        return None
    return round(sum(window), 2)


def _sum_forward(values: list[float | None], start_idx: int, hours: int) -> float | None:
    window = [v for v in values[start_idx + 1 : start_idx + 1 + hours] if v is not None]
    if not window:
        return None
    return round(sum(window), 2)


def _linear_trend(values: list[float | None]) -> float:
    """Least-squares slope in mm/h per hour over the supplied hourly values."""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    n = len(pts)
    if n < 3:
        return 0.0
    mean_x = sum(p[0] for p in pts) / n
    mean_y = sum(p[1] for p in pts) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in pts)
    den = sum((x - mean_x) ** 2 for x, _ in pts)
    return round(num / den, 3) if den else 0.0


def _normalise(
    raw: dict[str, Any],
    location_id: str,
    latitude: float,
    longitude: float,
    *,
    freshness: str,
    age_minutes: float,
    note: str | None = None,
) -> dict[str, Any]:
    report = ValidationReport()
    current = raw.get("current") or {}
    hourly = raw.get("hourly") or {}

    times: list[str] = list(hourly.get("time") or [])
    precip: list[float | None] = list(hourly.get("precipitation") or [])
    prob: list[float | None] = list(hourly.get("precipitation_probability") or [])
    temps: list[float | None] = list(hourly.get("temperature_2m") or [])

    if not times:
        report.errors.append("hourly series missing from upstream payload")

    # Locate "now" within the hourly series.
    now_idx = _locate_now(times, current.get("time"))

    accumulation = {
        "rain_1h": _sum_window(precip, now_idx, 1),
        "rain_3h": _sum_window(precip, now_idx, 3),
        "rain_6h": _sum_window(precip, now_idx, 6),
        "rain_12h": _sum_window(precip, now_idx, 12),
        "rain_24h": _sum_window(precip, now_idx, 24),
        "rain_48h": _sum_window(precip, now_idx, 48),
    }
    for k in list(accumulation):
        accumulation[k] = clean_number(accumulation[k], k, report, default=0.0)
    check_monotonic_accumulation(accumulation, report)

    forecast_window = precip[now_idx + 1 : now_idx + 1 + 24]
    peak_idx = None
    if forecast_window:
        vals = [(v if v is not None else -1.0) for v in forecast_window]
        peak_idx = max(range(len(vals)), key=lambda i: vals[i])

    forecast = {
        "rain_6h": _sum_forward(precip, now_idx, 6),
        "rain_12h": _sum_forward(precip, now_idx, 12),
        "rain_24h": _sum_forward(precip, now_idx, 24),
        "rain_48h": _sum_forward(precip, now_idx, 48),
        "max_hourly_next_24h": (
            round(max(v for v in forecast_window if v is not None), 2)
            if any(v is not None for v in forecast_window) else None
        ),
        "peak_time": (
            times[now_idx + 1 + peak_idx]
            if peak_idx is not None and now_idx + 1 + peak_idx < len(times) else None
        ),
        "max_probability_next_24h": (
            max((p for p in prob[now_idx + 1 : now_idx + 25] if p is not None), default=None)
        ),
    }
    forecast["rain_24h"] = clean_number(forecast["rain_24h"], "forecast_24h", report, default=0.0)

    trend_slope = _linear_trend(precip[max(0, now_idx - 5) : now_idx + 1])
    if trend_slope > 0.5:
        direction = "RISING"
    elif trend_slope < -0.5:
        direction = "FALLING"
    else:
        direction = "STEADY"

    cur = {
        "precipitation_mm_h": clean_number(
            current.get("precipitation"), "precipitation_mm_h", report, default=0.0
        ),
        "rain_mm_h": clean_number(current.get("rain"), "precipitation_mm_h", report, default=0.0),
        "temperature_c": clean_number(current.get("temperature_2m"), "temperature_c", report),
        "apparent_temperature_c": clean_number(
            current.get("apparent_temperature"), "temperature_c", report
        ),
        "humidity_pct": clean_number(
            current.get("relative_humidity_2m"), "humidity_pct", report
        ),
        "wind_kmh": clean_number(current.get("wind_speed_10m"), "wind_kmh", report),
        "wind_direction_deg": current.get("wind_direction_10m"),
        "pressure_hpa": clean_number(current.get("surface_pressure"), "pressure_hpa", report),
        "pressure_msl_hpa": clean_number(current.get("pressure_msl"), "pressure_hpa", report),
        "cloud_cover_pct": current.get("cloud_cover"),
        "weather_code": current.get("weather_code"),
        "observed_at": current.get("time"),
    }

    # 48 hours of history is retained so that exact 24-hour rolling windows can
    # be reconstructed for the retrospective risk timeline.
    past_series = dedupe_series([
        {
            "time": times[i],
            "precipitation": precip[i] if i < len(precip) else None,
            "temperature": temps[i] if i < len(temps) else None,
        }
        for i in range(max(0, now_idx - 47), min(now_idx + 1, len(times)))
    ])
    forecast_series = dedupe_series([
        {
            "time": times[i],
            "precipitation": precip[i] if i < len(precip) else None,
            "probability": prob[i] if i < len(prob) else None,
        }
        for i in range(now_idx + 1, min(now_idx + 25, len(times)))
    ])

    notes = [note] if note else []
    return {
        "location_id": location_id,
        "latitude": latitude,
        "longitude": longitude,
        "source": SOURCE_LABEL,
        "source_key": SOURCE_KEY,
        "attribution": SOURCE_ATTRIBUTION,
        "freshness": freshness,
        "age_minutes": round(age_minutes, 1),
        "observed_at": cur["observed_at"] or iso(utcnow()),
        "retrieved_at": iso(utcnow()),
        "timezone": raw.get("timezone"),
        "utc_offset_seconds": raw.get("utc_offset_seconds"),
        "model_elevation_m": raw.get("elevation"),
        "current": cur,
        "accumulation": accumulation,
        "forecast": forecast,
        "trend": {"slope_mm_per_h2": trend_slope, "direction": direction},
        "series": {"past": past_series, "forecast": forecast_series},
        "validation": report.to_dict(),
        "notes": notes,
    }


def _locate_now(times: list[str], current_time: str | None) -> int:
    if not times:
        return 0
    if current_time:
        stamp = current_time[:13]  # YYYY-MM-DDTHH
        for i, t in enumerate(times):
            if t[:13] == stamp:
                return i
    # Fall back to the boundary implied by the request window.
    return min(PAST_HOURS - 1, len(times) - 1)


# --------------------------------------------------------------------------
# Demo fallback - always clearly labelled
# --------------------------------------------------------------------------
def _demo_weather(
    location_id: str, latitude: float, longitude: float, *, reason: str
) -> dict[str, Any]:
    """Deterministic placeholder used only when live and cached data are both
    unavailable. Values are synthetic and marked DEMO end to end."""
    seed = int(hashlib.sha256(location_id.encode()).hexdigest()[:8], 16)
    base = (seed % 40) / 10.0                     # 0.0 - 3.9 mm/h
    temp = 12.0 + (seed % 150) / 10.0             # 12 - 27 C
    humidity = 55.0 + (seed % 400) / 10.0         # 55 - 95 %

    now = utcnow().replace(minute=0, second=0, microsecond=0)
    past, fcst = [], []
    for h in range(23, -1, -1):
        val = round(max(0.0, base * (0.5 + ((seed >> (h % 8)) % 10) / 10.0)), 2)
        past.append({"time": iso(now - timedelta(hours=h)), "precipitation": val, "temperature": temp})
    for h in range(1, 25):
        val = round(max(0.0, base * (0.4 + ((seed >> (h % 7)) % 12) / 12.0)), 2)
        fcst.append({"time": iso(now + timedelta(hours=h)), "precipitation": val, "probability": 30})

    acc = {
        "rain_1h": past[-1]["precipitation"],
        "rain_3h": round(sum(p["precipitation"] for p in past[-3:]), 2),
        "rain_6h": round(sum(p["precipitation"] for p in past[-6:]), 2),
        "rain_12h": round(sum(p["precipitation"] for p in past[-12:]), 2),
        "rain_24h": round(sum(p["precipitation"] for p in past), 2),
        "rain_48h": round(sum(p["precipitation"] for p in past) * 1.7, 2),
    }
    return {
        "location_id": location_id,
        "latitude": latitude,
        "longitude": longitude,
        "source": "Synthetic demonstration data",
        "source_key": SOURCE_KEY,
        "attribution": SOURCE_ATTRIBUTION,
        "freshness": "DEMO",
        "age_minutes": 0.0,
        "observed_at": iso(now),
        "retrieved_at": iso(utcnow()),
        "timezone": None,
        "utc_offset_seconds": None,
        "model_elevation_m": None,
        "current": {
            "precipitation_mm_h": round(base, 2),
            "rain_mm_h": round(base, 2),
            "temperature_c": round(temp, 1),
            "apparent_temperature_c": round(temp - 1.0, 1),
            "humidity_pct": round(humidity, 1),
            "wind_kmh": round(3.0 + (seed % 90) / 10.0, 1),
            "wind_direction_deg": seed % 360,
            "pressure_hpa": round(880.0 + (seed % 200) / 2.0, 1),
            "pressure_msl_hpa": 1008.0,
            "cloud_cover_pct": seed % 100,
            "weather_code": 61,
            "observed_at": iso(now),
        },
        "accumulation": acc,
        "forecast": {
            "rain_6h": round(sum(p["precipitation"] for p in fcst[:6]), 2),
            "rain_12h": round(sum(p["precipitation"] for p in fcst[:12]), 2),
            "rain_24h": round(sum(p["precipitation"] for p in fcst), 2),
            "rain_48h": round(sum(p["precipitation"] for p in fcst) * 1.8, 2),
            "max_hourly_next_24h": max(p["precipitation"] for p in fcst),
            "peak_time": fcst[0]["time"],
            "max_probability_next_24h": 30,
        },
        "trend": {"slope_mm_per_h2": 0.0, "direction": "STEADY"},
        "series": {"past": past, "forecast": fcst},
        "validation": {"ok": True, "errors": [], "warnings": [], "repaired": [], "dropped_fields": []},
        "notes": [
            "SYNTHETIC DEMONSTRATION DATA - not a measurement.",
            f"Live weather and cache both unavailable: {reason[:160]}",
        ],
    }
