"""SOURCE 3b - GIS / hydrological context (OpenStreetMap via Overpass API).

Provides three things:

1. **River proximity** - true point-to-polyline distance from each monitoring
   location to the nearest mapped waterway. Not a bounding-box approximation.
2. **Stream density** - mapped waterway length per square kilometre inside the
   search radius, a standard drainage-density measure.
3. **Exposure context** - hospitals, schools, bridges and settlements for the
   map and the authority view.

Overpass is a shared community resource. Every query here is bounded, batched
into as few requests as possible, and cached for a week.

Data (c) OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from app.config.logging_config import EV_DEMO, EV_FALLBACK, get_logger
from app.config.settings import settings
from app.services import data_cache
from app.services.data_cache import iso, utcnow
from app.services.http_client import UpstreamError, fetch_json

log = get_logger(__name__)

SOURCE_KEY = "overpass"
SOURCE_LABEL = "OpenStreetMap via Overpass API"
SOURCE_ATTRIBUTION = "Map data (c) OpenStreetMap contributors, ODbL"

SEARCH_RADIUS_M = 3500.0
MAX_POINTS_PER_WAY = 140


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
def _local_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Equirectangular projection to metres, accurate over a few km."""
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    return ((lon - lon0) * m_per_deg_lon, (lat - lat0) * m_per_deg_lat)


def _point_segment_distance_m(
    plat: float, plon: float,
    alat: float, alon: float,
    blat: float, blon: float,
) -> float:
    px, py = _local_xy(plat, plon, plat, plon)
    ax, ay = _local_xy(alat, alon, plat, plon)
    bx, by = _local_xy(blat, blon, plat, plon)
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _polyline_distance_m(
    plat: float, plon: float, coords: Sequence[tuple[float, float]]
) -> float:
    """Minimum distance from a point to a polyline, in metres."""
    if not coords:
        return float("inf")
    if len(coords) == 1:
        return _point_segment_distance_m(plat, plon, coords[0][0], coords[0][1],
                                         coords[0][0], coords[0][1])
    best = float("inf")
    for (alat, alon), (blat, blon) in zip(coords, coords[1:]):
        d = _point_segment_distance_m(plat, plon, alat, alon, blat, blon)
        if d < best:
            best = d
            if best < 1.0:
                break
    return best


def _polyline_length_m(coords: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for (alat, alon), (blat, blon) in zip(coords, coords[1:]):
        ax, ay = _local_xy(alat, alon, alat, alon)
        bx, by = _local_xy(blat, blon, alat, alon)
        total += math.hypot(bx - ax, by - ay)
    return total


def _clip_length_within_radius(
    plat: float, plon: float, coords: Sequence[tuple[float, float]], radius_m: float
) -> float:
    """Approximate length of a polyline lying inside a radius of a point."""
    total = 0.0
    for (alat, alon), (blat, blon) in zip(coords, coords[1:]):
        da = _point_segment_distance_m(plat, plon, alat, alon, alat, alon)
        db = _point_segment_distance_m(plat, plon, blat, blon, blat, blon)
        if da > radius_m and db > radius_m:
            continue
        ax, ay = _local_xy(alat, alon, plat, plon)
        bx, by = _local_xy(blat, blon, plat, plon)
        seg = math.hypot(bx - ax, by - ay)
        if da <= radius_m and db <= radius_m:
            total += seg
        else:
            total += seg * 0.5  # partially inside
    return total


def _decimate(coords: list[tuple[float, float]], limit: int = MAX_POINTS_PER_WAY) -> list[tuple[float, float]]:
    if len(coords) <= limit:
        return coords
    step = math.ceil(len(coords) / limit)
    out = coords[::step]
    if out[-1] != coords[-1]:
        out.append(coords[-1])
    return out


def _geometry_of(element: dict[str, Any]) -> list[tuple[float, float]]:
    geom = element.get("geometry") or []
    return [
        (float(g["lat"]), float(g["lon"]))
        for g in geom
        if isinstance(g, dict) and "lat" in g and "lon" in g
    ]


# --------------------------------------------------------------------------
# Overpass access
# --------------------------------------------------------------------------
async def _overpass(query: str, *, cache_key: str, ttl: int) -> tuple[Any, str, float]:
    """Run an Overpass query with mirror failover and stale-cache fallback.

    Returns (payload, freshness, age_minutes).
    """
    cached = data_cache.get(cache_key)
    if cached is not None:
        return cached.payload, "CACHED", cached.age_minutes

    last_error = "no mirror attempted"
    for url in settings.overpass_url_list:
        try:
            raw = await fetch_json(
                SOURCE_KEY, url, method="POST", data={"data": query},
                timeout=settings.http_overpass_timeout_seconds, retries=0,
            )
            if isinstance(raw, dict) and "elements" in raw:
                data_cache.put(cache_key, raw, source=SOURCE_KEY, ttl_seconds=ttl)
                return raw, "LIVE", 0.0
            last_error = "unexpected Overpass payload"
        except UpstreamError as exc:
            last_error = str(exc)
            log.warning("%s Overpass mirror %s failed: %s", EV_FALLBACK, url, str(exc)[:140])

    stale = data_cache.get(cache_key, allow_expired=True)
    if stale is not None:
        log.warning("%s serving stale OSM cache (age %.0f min)", EV_FALLBACK, stale.age_minutes)
        return stale.payload, "STALE_CACHE", stale.age_minutes

    raise UpstreamError(f"overpass: {last_error}")


# --------------------------------------------------------------------------
# River proximity for monitoring points
# --------------------------------------------------------------------------
async def get_river_context_batch(
    points: Sequence[tuple[str, float, float]], *, radius_m: float = SEARCH_RADIUS_M
) -> dict[str, dict[str, Any]]:
    """Nearest-waterway distance and drainage density for each point.

    All cache misses are resolved with a single Overpass request that unions one
    ``around`` clause per point.
    """
    out: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, float, float]] = []

    for pid, lat, lon in points:
        key = data_cache.make_key("osm-river", lat=lat, lon=lon, r=radius_m)
        cached = data_cache.get(key)
        if cached is not None:
            out[pid] = _river_result(pid, cached.payload, "CACHED", cached.age_minutes, radius_m)
        else:
            pending.append((pid, lat, lon))

    if not pending:
        return out

    clauses = "".join(
        f'way(around:{int(radius_m)},{lat:.5f},{lon:.5f})["waterway"~"^(river|stream|canal|drain)$"];'
        for _, lat, lon in pending
    )
    query = f"[out:json][timeout:{int(settings.http_overpass_timeout_seconds)}];({clauses});out geom;"
    bulk_key = data_cache.make_key(
        "osm-river-bulk",
        pts="|".join(f"{lat:.3f},{lon:.3f}" for _, lat, lon in pending),
        r=radius_m,
    )

    try:
        raw, freshness, age = await _overpass(query, cache_key=bulk_key, ttl=settings.cache_ttl_osm)
    except UpstreamError as exc:
        log.warning("%s river context unavailable: %s", EV_DEMO, str(exc)[:160])
        for pid, lat, lon in pending:
            out[pid] = _river_unavailable(pid, str(exc), radius_m)
        return out

    ways = [e for e in raw.get("elements", []) if e.get("type") == "way"]
    parsed = [
        (
            e.get("tags", {}).get("name"),
            e.get("tags", {}).get("waterway"),
            _geometry_of(e),
        )
        for e in ways
    ]
    parsed = [p for p in parsed if len(p[2]) >= 2]

    for pid, lat, lon in pending:
        nearest_d = float("inf")
        nearest_name: str | None = None
        nearest_type: str | None = None
        length_in_radius = 0.0
        rivers_within = 0
        streams_within = 0

        for name, wtype, coords in parsed:
            d = _polyline_distance_m(lat, lon, coords)
            if d < nearest_d:
                nearest_d, nearest_name, nearest_type = d, name, wtype
            if d <= radius_m:
                length_in_radius += _clip_length_within_radius(lat, lon, coords, radius_m)
                if wtype == "river":
                    rivers_within += 1
                elif wtype == "stream":
                    streams_within += 1

        area_km2 = math.pi * (radius_m / 1000.0) ** 2
        payload = {
            "river_distance_m": round(nearest_d, 1) if nearest_d != float("inf") else None,
            "nearest_waterway_name": nearest_name,
            "nearest_waterway_type": nearest_type,
            "stream_density_km_per_km2": round((length_in_radius / 1000.0) / area_km2, 3),
            "waterway_length_km": round(length_in_radius / 1000.0, 2),
            "rivers_within_radius": rivers_within,
            "streams_within_radius": streams_within,
            "search_radius_m": radius_m,
            "beyond_search_radius": nearest_d > radius_m,
        }
        data_cache.put(
            data_cache.make_key("osm-river", lat=lat, lon=lon, r=radius_m),
            payload, source="osm-river", ttl_seconds=settings.cache_ttl_osm,
        )
        out[pid] = _river_result(pid, payload, freshness, age, radius_m)

    return out


def _river_result(
    pid: str, payload: dict[str, Any], freshness: str, age: float, radius_m: float
) -> dict[str, Any]:
    return {
        "location_id": pid,
        "source": SOURCE_LABEL,
        "source_key": SOURCE_KEY,
        "attribution": SOURCE_ATTRIBUTION,
        "freshness": freshness,
        "age_minutes": round(age, 1),
        "observed_at": iso(utcnow()),
        "method": (
            "True point-to-polyline distance against OpenStreetMap waterway geometry; "
            f"drainage density is mapped waterway length per km2 within {radius_m/1000:.1f} km."
        ),
        **payload,
    }


def _river_unavailable(pid: str, reason: str, radius_m: float) -> dict[str, Any]:
    return {
        "location_id": pid,
        "source": "unavailable",
        "source_key": SOURCE_KEY,
        "attribution": SOURCE_ATTRIBUTION,
        "freshness": "DEMO",
        "age_minutes": 0.0,
        "observed_at": iso(utcnow()),
        "river_distance_m": None,
        "nearest_waterway_name": None,
        "nearest_waterway_type": None,
        "stream_density_km_per_km2": None,
        "waterway_length_km": None,
        "rivers_within_radius": 0,
        "streams_within_radius": 0,
        "search_radius_m": radius_m,
        "beyond_search_radius": True,
        "method": "unavailable",
        "notes": [
            "OpenStreetMap waterway data unavailable - river proximity is not being estimated.",
            f"Reason: {reason[:160]}",
        ],
    }


# --------------------------------------------------------------------------
# Region-wide GIS layers for the map
# --------------------------------------------------------------------------
async def get_region_waterways(region: dict[str, Any]) -> dict[str, Any]:
    bbox = region.get("gis_query_bbox") or region["bbox"]
    s, w, n, e = bbox["min_lat"], bbox["min_lon"], bbox["max_lat"], bbox["max_lon"]
    query = (
        f"[out:json][timeout:{int(settings.http_overpass_timeout_seconds)}];"
        f'(way["waterway"="river"]({s},{w},{n},{e});'
        f'way["waterway"="stream"]({s},{w},{n},{e}););'
        "out geom;"
    )
    key = data_cache.make_key("osm-waterways", region=region["id"], s=s, w=w, n=n, e=e)

    try:
        raw, freshness, age = await _overpass(query, cache_key=key, ttl=settings.cache_ttl_osm)
    except UpstreamError as exc:
        return {
            "region_id": region["id"], "freshness": "DEMO", "age_minutes": 0.0,
            "source": SOURCE_LABEL, "source_key": SOURCE_KEY,
            "attribution": SOURCE_ATTRIBUTION, "bbox": bbox,
            "rivers": [], "streams": [], "count": 0,
            "notes": [f"OpenStreetMap waterways unavailable: {str(exc)[:160]}"],
            "observed_at": iso(utcnow()),
        }

    rivers: list[dict[str, Any]] = []
    streams: list[dict[str, Any]] = []
    for el in raw.get("elements", []):
        if el.get("type") != "way":
            continue
        coords = _decimate(_geometry_of(el))
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        item = {
            "id": el.get("id"),
            "name": tags.get("name"),
            "waterway": tags.get("waterway"),
            "coordinates": [[round(a, 5), round(b, 5)] for a, b in coords],
        }
        (rivers if tags.get("waterway") == "river" else streams).append(item)

    log.info("OSM waterways for %s: %d rivers, %d streams (%s)",
             region["id"], len(rivers), len(streams), freshness)
    return {
        "region_id": region["id"], "freshness": freshness, "age_minutes": round(age, 1),
        "source": SOURCE_LABEL, "source_key": SOURCE_KEY,
        "attribution": SOURCE_ATTRIBUTION, "bbox": bbox,
        "rivers": rivers, "streams": streams,
        "count": len(rivers) + len(streams),
        "named_rivers": sorted({r["name"] for r in rivers if r.get("name")}),
        "notes": [], "observed_at": iso(utcnow()),
    }


def river_context_from_dataset(
    points: Sequence[tuple[str, float, float]],
    waterways: dict[str, Any],
    *,
    radius_m: float = SEARCH_RADIUS_M,
) -> dict[str, dict[str, Any]]:
    """Compute river proximity and drainage density locally from an already
    fetched waterway dataset.

    The risk grid uses this instead of issuing its own Overpass queries: the
    region waterway layer is fetched once for the map, and every grid cell's
    distance is then computed against that same real geometry. Same rigour, no
    additional load on a shared community service.
    """
    lines: list[tuple[str | None, str | None, list[tuple[float, float]]]] = []
    for bucket in ("rivers", "streams"):
        for item in waterways.get(bucket, []) or []:
            coords = [(float(a), float(b)) for a, b in item.get("coordinates", [])]
            if len(coords) >= 2:
                lines.append((item.get("name"), item.get("waterway"), coords))

    freshness = waterways.get("freshness", "DEMO")
    age = waterways.get("age_minutes", 0.0)
    area_km2 = math.pi * (radius_m / 1000.0) ** 2
    out: dict[str, dict[str, Any]] = {}

    for pid, lat, lon in points:
        if not lines:
            out[pid] = _river_unavailable(pid, "no waterway geometry available", radius_m)
            continue

        nearest_d = float("inf")
        nearest_name = nearest_type = None
        length_in_radius = 0.0
        rivers_within = streams_within = 0

        for name, wtype, coords in lines:
            # Cheap bounding-box rejection before the exact distance computation.
            if min(c[0] for c in coords) - lat > 0.06 or lat - max(c[0] for c in coords) > 0.06:
                if nearest_d < radius_m:
                    continue
            d = _polyline_distance_m(lat, lon, coords)
            if d < nearest_d:
                nearest_d, nearest_name, nearest_type = d, name, wtype
            if d <= radius_m:
                length_in_radius += _clip_length_within_radius(lat, lon, coords, radius_m)
                if wtype == "river":
                    rivers_within += 1
                else:
                    streams_within += 1

        payload = {
            "river_distance_m": round(nearest_d, 1) if nearest_d != float("inf") else None,
            "nearest_waterway_name": nearest_name,
            "nearest_waterway_type": nearest_type,
            "stream_density_km_per_km2": round((length_in_radius / 1000.0) / area_km2, 3),
            "waterway_length_km": round(length_in_radius / 1000.0, 2),
            "rivers_within_radius": rivers_within,
            "streams_within_radius": streams_within,
            "search_radius_m": radius_m,
            "beyond_search_radius": nearest_d > radius_m,
        }
        out[pid] = _river_result(pid, payload, freshness, age, radius_m)
    return out


async def get_region_infrastructure(region: dict[str, Any]) -> dict[str, Any]:
    """Hospitals, schools, bridges and settlements inside the pilot corridor."""
    bbox = region.get("gis_query_bbox") or region["bbox"]
    s, w, n, e = bbox["min_lat"], bbox["min_lon"], bbox["max_lat"], bbox["max_lon"]
    box = f"({s},{w},{n},{e})"
    query = (
        f"[out:json][timeout:{int(settings.http_overpass_timeout_seconds)}];("
        f'node["amenity"~"^(hospital|clinic)$"]{box};'
        f'way["amenity"~"^(hospital|clinic)$"]{box};'
        f'node["amenity"="school"]{box};'
        f'node["place"~"^(city|town|village)$"]{box};'
        f'way["man_made"="bridge"]{box};'
        f'way["bridge"="yes"]["highway"]{box};'
        # No element cap. `out center 900` silently truncated the union at 900
        # elements, and because bridges come last it cut them roughly in half —
        # Uttarakhand reported 900 features when the real figure is 1361. The
        # small categories were complete, so the total looked plausible.
        ");out center;"
    )
    key = data_cache.make_key("osm-infra", region=region["id"], s=s, w=w, n=n, e=e)

    try:
        raw, freshness, age = await _overpass(query, cache_key=key, ttl=settings.cache_ttl_osm)
    except UpstreamError as exc:
        return {
            "region_id": region["id"], "freshness": "DEMO", "age_minutes": 0.0,
            "source": SOURCE_LABEL, "source_key": SOURCE_KEY,
            "attribution": SOURCE_ATTRIBUTION, "bbox": bbox, "features": [],
            "counts": {}, "notes": [f"OpenStreetMap infrastructure unavailable: {str(exc)[:160]}"],
            "observed_at": iso(utcnow()),
        }

    features: list[dict[str, Any]] = []
    for el in raw.get("elements", []):
        tags = el.get("tags", {}) or {}
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        amenity, place = tags.get("amenity"), tags.get("place")
        if amenity in {"hospital", "clinic"}:
            kind = "hospital"
        elif amenity == "school":
            kind = "school"
        elif place in {"city", "town", "village"}:
            kind = "settlement"
        elif tags.get("man_made") == "bridge" or tags.get("bridge") == "yes":
            kind = "bridge"
        else:
            continue
        features.append({
            "id": f"{el.get('type')}/{el.get('id')}",
            "kind": kind,
            "name": tags.get("name"),
            "subtype": place or amenity,
            "latitude": round(float(lat), 5),
            "longitude": round(float(lon), 5),
        })

    counts: dict[str, int] = {}
    for f in features:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1

    log.info("OSM infrastructure for %s: %s (%s)", region["id"], counts, freshness)
    return {
        "region_id": region["id"], "freshness": freshness, "age_minutes": round(age, 1),
        "source": SOURCE_LABEL, "source_key": SOURCE_KEY,
        "attribution": SOURCE_ATTRIBUTION, "bbox": bbox,
        "features": features, "counts": counts, "notes": [],
        "observed_at": iso(utcnow()),
    }
