"""SOURCE 2 - Terrain / elevation.

Primary source is the Open-Meteo Elevation API, which serves the Copernicus
DEM GLO-90 digital elevation model. Two independent public elevation services
are kept as fallbacks.

Slope and local relief are not taken from any API: they are *derived* here by
sampling a 3x3 stencil of DEM points around each location and computing a
finite-difference gradient. Terrain is effectively static, so results are
cached for months and one prefetch covers the whole region.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from app.config.logging_config import EV_DEMO, EV_FALLBACK, get_logger
from app.config.settings import settings
from app.services import data_cache
from app.services.data_cache import iso, utcnow
from app.services.http_client import UpstreamError, fetch_json
from app.services.validation import ValidationReport, clean_number, valid_coordinates

log = get_logger(__name__)

SOURCE_KEY = "open-meteo-elevation"
SOURCE_LABEL = "Open-Meteo Elevation API (Copernicus DEM GLO-90)"
SOURCE_ATTRIBUTION = "Elevation from Copernicus DEM GLO-90 via Open-Meteo; fallbacks: OpenTopoData (SRTM), Open-Elevation"

# Half-width of the DEM sampling stencil, in metres. 500 m gives a hillslope
# gradient at catchment scale rather than pixel noise on a 90 m DEM.
STENCIL_M = 500.0
METRES_PER_DEG_LAT = 111_320.0


def _offsets(lat: float, lon: float, d_m: float = STENCIL_M) -> list[tuple[float, float]]:
    """3x3 stencil: centre plus 8 neighbours, ordered row-major (N to S)."""
    dlat = d_m / METRES_PER_DEG_LAT
    dlon = d_m / (METRES_PER_DEG_LAT * max(0.15, math.cos(math.radians(lat))))
    return [
        (lat + dlat, lon - dlon), (lat + dlat, lon), (lat + dlat, lon + dlon),
        (lat,        lon - dlon), (lat,        lon), (lat,        lon + dlon),
        (lat - dlat, lon - dlon), (lat - dlat, lon), (lat - dlat, lon + dlon),
    ]


def _slope_and_relief(elevs: list[float | None], lat: float, d_m: float = STENCIL_M) -> tuple[float | None, float | None, float | None]:
    """Finite-difference slope (degrees), aspect (degrees) and relief (metres).

    Uses the 4-neighbour central difference (indices 1,3,5,7 of the stencil),
    falling back to the centre value where a neighbour is missing.
    """
    valid = [e for e in elevs if e is not None]
    if not valid:
        return None, None, None

    centre = elevs[4] if elevs[4] is not None else sum(valid) / len(valid)
    north = elevs[1] if elevs[1] is not None else centre
    south = elevs[7] if elevs[7] is not None else centre
    west = elevs[3] if elevs[3] is not None else centre
    east = elevs[5] if elevs[5] is not None else centre

    dz_dx = (east - west) / (2 * d_m)     # positive eastwards
    dz_dy = (north - south) / (2 * d_m)   # positive northwards
    slope_deg = math.degrees(math.atan(math.hypot(dz_dx, dz_dy)))

    aspect = None
    if abs(dz_dx) > 1e-9 or abs(dz_dy) > 1e-9:
        aspect = (math.degrees(math.atan2(dz_dy, -dz_dx)) + 360.0) % 360.0

    relief = max(valid) - min(valid)
    return round(slope_deg, 2), (round(aspect, 1) if aspect is not None else None), round(relief, 1)


# --------------------------------------------------------------------------
# Raw elevation lookup with a three-provider fallback chain
# --------------------------------------------------------------------------
async def _fetch_elevations(points: Sequence[tuple[float, float]]) -> tuple[list[float | None], str, str]:
    """Return (elevations, source_key, source_label) for the supplied points."""
    lats = ",".join(f"{p[0]:.5f}" for p in points)
    lons = ",".join(f"{p[1]:.5f}" for p in points)

    try:
        raw = await fetch_json(
            SOURCE_KEY, settings.open_meteo_elevation_url,
            params={"latitude": lats, "longitude": lons},
        )
        values = raw.get("elevation") if isinstance(raw, dict) else None
        if isinstance(values, list) and len(values) == len(points):
            return [_f(v) for v in values], SOURCE_KEY, SOURCE_LABEL
        raise UpstreamError("unexpected elevation payload shape")
    except UpstreamError as exc:
        log.warning("%s open-meteo elevation failed (%s), trying OpenTopoData", EV_FALLBACK, exc)

    try:
        locs = "|".join(f"{p[0]:.5f},{p[1]:.5f}" for p in points)
        raw = await fetch_json(
            "open-topo-data", settings.open_topo_data_url, params={"locations": locs}
        )
        results = raw.get("results") if isinstance(raw, dict) else None
        if isinstance(results, list) and len(results) == len(points):
            return (
                [_f(r.get("elevation")) for r in results],
                "open-topo-data",
                "OpenTopoData (SRTM 90 m)",
            )
        raise UpstreamError("unexpected OpenTopoData payload shape")
    except UpstreamError as exc:
        log.warning("%s OpenTopoData failed (%s), trying Open-Elevation", EV_FALLBACK, exc)

    try:
        locs = "|".join(f"{p[0]:.5f},{p[1]:.5f}" for p in points)
        raw = await fetch_json(
            "open-elevation", settings.open_elevation_url, params={"locations": locs}
        )
        results = raw.get("results") if isinstance(raw, dict) else None
        if isinstance(results, list) and len(results) == len(points):
            return (
                [_f(r.get("elevation")) for r in results],
                "open-elevation",
                "Open-Elevation (SRTM)",
            )
    except UpstreamError as exc:
        log.warning("%s Open-Elevation failed (%s)", EV_FALLBACK, exc)

    raise UpstreamError("all elevation providers unavailable")


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
async def get_terrain(
    location_id: str,
    latitude: float,
    longitude: float,
    *,
    reference_elevation_m: float | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    results = await get_terrain_batch(
        [(location_id, latitude, longitude, reference_elevation_m)],
        force_refresh=force_refresh,
    )
    return results[location_id]


async def get_terrain_batch(
    points: Sequence[tuple[str, float, float, float | None]],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Terrain for many points. Each point costs 9 DEM samples; those are
    batched across points into as few HTTP calls as possible and cached for
    months because terrain does not change."""
    out: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, float, float, float | None]] = []

    for pid, lat, lon, ref in points:
        if not valid_coordinates(lat, lon):
            out[pid] = _terrain_error(pid, lat, lon, "invalid coordinates")
            continue
        key = data_cache.make_key("terrain", lat=lat, lon=lon, d=STENCIL_M)
        cached = None if force_refresh else data_cache.get(key)
        if cached is not None:
            out[pid] = _build(pid, lat, lon, cached.payload, freshness="CACHED",
                              age_minutes=cached.age_minutes)
        else:
            pending.append((pid, lat, lon, ref))

    # Build one flat list of stencil points across all pending locations.
    stencil_points: list[tuple[float, float]] = []
    spans: list[tuple[str, float, float, float | None, int, int]] = []
    for pid, lat, lon, ref in pending:
        start = len(stencil_points)
        stencil_points.extend(_offsets(lat, lon))
        spans.append((pid, lat, lon, ref, start, len(stencil_points)))

    elevations: list[float | None] = [None] * len(stencil_points)
    src_key, src_label = SOURCE_KEY, SOURCE_LABEL
    fetch_failed: str | None = None

    for i in range(0, len(stencil_points), settings.max_batch_points):
        chunk = stencil_points[i : i + settings.max_batch_points]
        try:
            vals, src_key, src_label = await _fetch_elevations(chunk)
            elevations[i : i + len(chunk)] = vals
        except UpstreamError as exc:
            fetch_failed = str(exc)
            log.warning("%s elevation chunk %d failed: %s", EV_FALLBACK, i, exc)

    for pid, lat, lon, ref, start, end in spans:
        block = elevations[start:end]
        if any(v is not None for v in block):
            slope, aspect, relief = _slope_and_relief(block, lat)
            payload = {
                "elevation_m": block[4] if block[4] is not None else next(v for v in block if v is not None),
                "slope_deg": slope,
                "aspect_deg": aspect,
                "relief_m": relief,
                "samples": block,
                "stencil_m": STENCIL_M,
                "source_key": src_key,
                "source_label": src_label,
            }
            data_cache.put(
                data_cache.make_key("terrain", lat=lat, lon=lon, d=STENCIL_M),
                payload, source="terrain", ttl_seconds=settings.cache_ttl_elevation,
            )
            out[pid] = _build(pid, lat, lon, payload, freshness="LIVE", age_minutes=0.0)
        else:
            stale = data_cache.get(
                data_cache.make_key("terrain", lat=lat, lon=lon, d=STENCIL_M), allow_expired=True
            )
            if stale is not None:
                out[pid] = _build(pid, lat, lon, stale.payload, freshness="STALE_CACHE",
                                  age_minutes=stale.age_minutes,
                                  note="Live DEM unavailable; using cached terrain.")
            else:
                out[pid] = _demo_terrain(pid, lat, lon, ref, fetch_failed or "no elevation data")

    return out


def _build(
    location_id: str, lat: float, lon: float, payload: dict[str, Any],
    *, freshness: str, age_minutes: float, note: str | None = None,
) -> dict[str, Any]:
    report = ValidationReport()
    elevation = clean_number(payload.get("elevation_m"), "elevation_m", report)
    slope = clean_number(payload.get("slope_deg"), "slope_deg", report, default=0.0)
    relief = clean_number(payload.get("relief_m"), "relief_m", report, default=0.0)

    return {
        "location_id": location_id,
        "latitude": lat,
        "longitude": lon,
        "source": payload.get("source_label", SOURCE_LABEL),
        "source_key": payload.get("source_key", SOURCE_KEY),
        "attribution": SOURCE_ATTRIBUTION,
        "freshness": freshness,
        "age_minutes": round(age_minutes, 1),
        "observed_at": iso(utcnow()),
        "elevation_m": elevation,
        "slope_deg": slope,
        "aspect_deg": payload.get("aspect_deg"),
        "aspect_cardinal": _cardinal(payload.get("aspect_deg")),
        "relief_m": relief,
        "terrain_class": _terrain_class(slope),
        "elevation_band": _elevation_band(elevation),
        "stencil_m": payload.get("stencil_m", STENCIL_M),
        "samples": payload.get("samples"),
        "method": (
            f"Slope and relief derived from a 3x3 DEM stencil at "
            f"+/-{payload.get('stencil_m', STENCIL_M):.0f} m using central differences."
        ),
        "validation": report.to_dict(),
        "notes": [note] if note else [],
    }


def _cardinal(deg: float | None) -> str | None:
    if deg is None:
        return None
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg + 22.5) % 360 // 45)]


def _terrain_class(slope: float | None) -> str:
    if slope is None:
        return "unknown"
    if slope < 3:
        return "flat"
    if slope < 8:
        return "gentle"
    if slope < 16:
        return "moderate"
    if slope < 30:
        return "steep"
    return "very steep"


def _elevation_band(e: float | None) -> str:
    if e is None:
        return "unknown"
    if e < 500:
        return "valley / foothill"
    if e < 1500:
        return "lower hill"
    if e < 2500:
        return "mid mountain"
    if e < 3500:
        return "high mountain"
    return "alpine / glacial"


def _terrain_error(pid: str, lat: float, lon: float, reason: str) -> dict[str, Any]:
    report = ValidationReport()
    report.errors.append(reason)
    return {
        "location_id": pid, "latitude": lat, "longitude": lon,
        "source": "n/a", "source_key": SOURCE_KEY, "attribution": SOURCE_ATTRIBUTION,
        "freshness": "DEMO", "age_minutes": 0.0, "observed_at": iso(utcnow()),
        "elevation_m": None, "slope_deg": None, "aspect_deg": None, "aspect_cardinal": None,
        "relief_m": None, "terrain_class": "unknown", "elevation_band": "unknown",
        "stencil_m": STENCIL_M, "samples": None, "method": "unavailable",
        "validation": report.to_dict(), "notes": [f"Terrain unavailable: {reason}"],
    }


def _demo_terrain(
    pid: str, lat: float, lon: float, reference_elevation_m: float | None, reason: str
) -> dict[str, Any]:
    """Fallback terrain. Prefers the published reference elevation from the
    region configuration (a real public figure) over anything invented, and
    labels the derived slope as an estimate."""
    log.warning("%s terrain fallback for %s (%s)", EV_DEMO, pid, reason[:120])
    elevation = reference_elevation_m
    slope = 18.0 if (elevation or 0) > 1000 else 8.0
    relief = 400.0 if (elevation or 0) > 1000 else 120.0
    notes = [
        "DEMO TERRAIN - live DEM and cache both unavailable.",
        f"Reason: {reason[:160]}",
    ]
    if elevation is not None:
        notes.append(
            "Elevation is the published reference value from the region configuration, "
            "not a live DEM sample. Slope and relief are coarse estimates."
        )
    else:
        notes.append("SYNTHETIC DEMONSTRATION DATA - no elevation reference available.")
        elevation = 1200.0

    return {
        "location_id": pid, "latitude": lat, "longitude": lon,
        "source": "Region configuration reference elevation (offline fallback)",
        "source_key": "fallback-reference",
        "attribution": SOURCE_ATTRIBUTION,
        "freshness": "DEMO", "age_minutes": 0.0, "observed_at": iso(utcnow()),
        "elevation_m": elevation, "slope_deg": slope, "aspect_deg": None,
        "aspect_cardinal": None, "relief_m": relief,
        "terrain_class": _terrain_class(slope), "elevation_band": _elevation_band(elevation),
        "stencil_m": STENCIL_M, "samples": None,
        "method": "Offline fallback - not derived from a live DEM.",
        "validation": {"ok": True, "errors": [], "warnings": [], "repaired": [], "dropped_fields": []},
        "notes": notes,
    }
