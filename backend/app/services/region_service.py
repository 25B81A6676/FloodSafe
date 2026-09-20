"""Region and monitoring-location registry.

Regions are pure configuration (``data/regions/*.json``). Nothing about
Uttarakhand is hard-coded in the business logic: adding another hilly region is
a matter of dropping in a new JSON file.

Beyond those curated files the registry falls back to ``india_service``, which
synthesises a region of the same shape for any Indian state or district from the
OpenStreetMap administrative dataset. Resolution order is always **curated
first**, so the two pilot regions keep their hand-checked rivers, confluences and
monitoring locations, and every other part of India is served from real OSM
boundaries through the identical code path.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.db import get_conn, write_conn
from app.services import india_service
from app.services.data_cache import iso, utcnow

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Location:
    id: str
    region_id: str
    name: str
    district: str | None
    latitude: float
    longitude: float
    reference_elevation_m: float | None
    nearest_river: str | None
    settlement_type: str | None
    exposure: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "region_id": self.region_id,
            "name": self.name,
            "district": self.district,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "reference_elevation_m": self.reference_elevation_m,
            "nearest_river": self.nearest_river,
            "settlement_type": self.settlement_type,
            "exposure": self.exposure,
        }


class RegionNotFound(KeyError):
    pass


class LocationNotFound(KeyError):
    pass


@lru_cache(maxsize=1)
def _load_all() -> dict[str, dict[str, Any]]:
    regions: dict[str, dict[str, Any]] = {}
    if not settings.regions_dir.exists():
        log.error("regions directory missing: %s", settings.regions_dir)
        return regions
    for path in sorted(settings.regions_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.error("region file %s is invalid JSON: %s", path.name, exc)
            continue
        rid = data.get("id")
        if not rid:
            log.error("region file %s has no id", path.name)
            continue
        if not data.get("enabled", True):
            continue
        regions[rid] = data
    log.info("loaded %d region(s): %s", len(regions), ", ".join(sorted(regions)))
    return regions


def reload_regions() -> None:
    _load_all.cache_clear()
    _load_all()


def list_regions() -> list[dict[str, Any]]:
    out = []
    for rid, r in sorted(_load_all().items()):
        out.append(
            {
                "id": rid,
                "name": r["name"],
                "display_name": r.get("display_name", r["name"]),
                "country": r.get("country"),
                "terrain_type": r.get("terrain_type"),
                "description": r.get("description"),
                "bbox": r["bbox"],
                "center": r["center"],
                "default_zoom": r.get("default_zoom", 8),
                "timezone": r.get("timezone", "UTC"),
                "location_count": len(r.get("monitoring_locations", [])),
                "rivers": r.get("rivers", []),
                "elevation_range_m": r.get("elevation_range_m"),
                "is_default": rid == settings.default_region_id,
            }
        )
    return out


def _load_seed_for(region_id: str) -> None:
    """Load the bundled snapshot for a region, once per process. Never raises:
    without it everything is simply fetched live, which is the old behaviour."""
    from app.services import seed_cache

    try:
        # A district (``kerala__wayanad``) is served by its state's shard.
        seed_cache.load_seed(region_id.split("__", 1)[0])
    except Exception as exc:  # noqa: BLE001 - a snapshot must never break a request
        log.warning("could not load the bundled snapshot for %s: %s", region_id, exc)


def get_region(region_id: str | None = None) -> dict[str, Any]:
    """Curated region file if one exists, otherwise a synthesised Indian scope.

    ``india``, any state slug (``kerala``) and any district slug
    (``kerala__wayanad``) resolve here, which is what lets every downstream
    service work nationwide without knowing that more than one kind of region
    exists.
    """
    rid = region_id or settings.default_region_id
    # Bring this region's bundled geometry into the cache before anything asks
    # for it. Sharded, so a cold start pays for the one region in use rather
    # than for the whole country - see seed_cache.load_seed.
    _load_seed_for(rid)
    regions = _load_all()
    if rid in regions:
        return regions[rid]
    try:
        return india_service.synthesize_region(rid)
    except (india_service.ScopeNotFound, india_service.GeographyUnavailable) as exc:
        raise RegionNotFound(rid) from exc


def get_locations(region_id: str | None = None) -> list[Location]:
    region = get_region(region_id)
    rid = region["id"]
    return [
        Location(
            id=raw["id"],
            region_id=rid,
            name=raw["name"],
            district=raw.get("district"),
            latitude=float(raw["latitude"]),
            longitude=float(raw["longitude"]),
            reference_elevation_m=raw.get("reference_elevation_m"),
            nearest_river=raw.get("nearest_river"),
            settlement_type=raw.get("settlement_type"),
            exposure=raw.get("exposure"),
        )
        for raw in region.get("monitoring_locations", [])
    ]


@lru_cache(maxsize=1)
def _location_index() -> dict[str, Location]:
    idx: dict[str, Location] = {}
    for rid in _load_all():
        for loc in get_locations(rid):
            idx[loc.id] = loc
    return idx


def _synthesized_location(location_id: str) -> Location | None:
    """Resolve a location id that belongs to a synthesised Indian scope.

    Centroid ids carry their own geography, so they resolve with no lookup
    table. Settlement ids (``loc_osm_*``) are resolved from OpenStreetMap the
    first time a district is opened and persisted to SQLite, so they resolve
    here on every later request - including after a restart.
    """
    if location_id.startswith("loc_state_"):
        try:
            state = india_service.get_state(location_id.removeprefix("loc_state_"))
        except india_service.ScopeNotFound:
            return None
        point = india_service.representative_point(state)
        return Location(
            id=location_id, region_id=state["id"], name=state["name"], district=None,
            latitude=float(point["latitude"]), longitude=float(point["longitude"]),
            reference_elevation_m=None, nearest_river=None,
            settlement_type=(
                "state_admin_centre"
                if point["source"] == "osm_admin_centre"
                else "state_centroid"
            ),
            exposure=None,
        )

    candidate = location_id.removeprefix("loc_")
    if india_service.DISTRICT_SEP in candidate:
        try:
            district = india_service.get_district(candidate)
        except india_service.ScopeNotFound:
            return None
        return Location(
            id=location_id, region_id=district["id"],
            name=f"{district['name']} district centre", district=district["name"],
            latitude=district["center"]["latitude"], longitude=district["center"]["longitude"],
            reference_elevation_m=None, nearest_river=None,
            settlement_type="district_centroid", exposure=None,
        )

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM monitoring_locations WHERE id = ?", (location_id,)
        ).fetchone()
    if row is None:
        return None
    return Location(
        id=row["id"], region_id=row["region_id"], name=row["name"],
        district=row["district"], latitude=row["latitude"], longitude=row["longitude"],
        reference_elevation_m=row["elevation_m"], nearest_river=row["nearest_river"],
        settlement_type=row["settlement_type"], exposure=row["exposure"],
    )


def get_location(location_id: str) -> Location:
    idx = _location_index()
    if location_id in idx:
        return idx[location_id]
    synthesized = _synthesized_location(location_id)
    if synthesized is None:
        raise LocationNotFound(location_id)
    return synthesized


def region_of(location_id: str) -> dict[str, Any]:
    return get_region(get_location(location_id).region_id)


# --------------------------------------------------------------------------
# Risk grid
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GridCell:
    id: str
    row: int
    col: int
    center_lat: float
    center_lon: float
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "row": self.row,
            "col": self.col,
            "center": {"latitude": self.center_lat, "longitude": self.center_lon},
            "bounds": {
                "min_lat": self.min_lat,
                "min_lon": self.min_lon,
                "max_lat": self.max_lat,
                "max_lon": self.max_lon,
            },
        }


def get_grid_cells(region_id: str | None = None) -> list[GridCell]:
    """Discrete monitoring cells over the region's core corridor.

    Each cell is evaluated from its own real data, so the resulting map layer is
    a grid of independent assessments rather than an interpolated surface.
    """
    region = get_region(region_id)
    cfg = region.get("risk_grid") or {}
    rows = int(cfg.get("rows", 5))
    cols = int(cfg.get("cols", 5))
    bbox = cfg.get("bbox") or region["bbox"]
    mask = cfg.get("mask")

    lat0, lat1 = float(bbox["min_lat"]), float(bbox["max_lat"])
    lon0, lon1 = float(bbox["min_lon"]), float(bbox["max_lon"])
    dlat = (lat1 - lat0) / rows
    dlon = (lon1 - lon0) / cols

    cells: list[GridCell] = []
    for r in range(rows):
        for c in range(cols):
            mnlat = lat0 + r * dlat
            mnlon = lon0 + c * dlon
            clat = round(mnlat + dlat / 2, 5)
            clon = round(mnlon + dlon / 2, 5)
            # India's bounding box is mostly not India - a uniform grid over it
            # puts cells in the Bay of Bengal and the Arabian Sea, where four
            # upstream APIs would be queried for nothing. Keep only cells whose
            # centre falls inside some state.
            if mask == "state_bbox" and not india_service.in_india(clat, clon):
                continue
            cells.append(
                GridCell(
                    id=f"cell_{r}_{c}",
                    row=r,
                    col=c,
                    center_lat=clat,
                    center_lon=clon,
                    min_lat=round(mnlat, 5),
                    min_lon=round(mnlon, 5),
                    max_lat=round(mnlat + dlat, 5),
                    max_lon=round(mnlon + dlon, 5),
                )
            )
    return cells


# --------------------------------------------------------------------------
# Geo helpers
# --------------------------------------------------------------------------
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def sync_locations_to_db() -> int:
    """Mirror the configured locations into SQLite (spec table)."""
    n = 0
    now = iso(utcnow())
    # Resolve every location BEFORE opening the write transaction. Reading
    # inside it deadlocks: get_locations resolves a region, resolving a region
    # loads its bundled snapshot, and loading writes - to a second connection,
    # against a write lock this one is still holding.
    resolved = [loc for rid in _load_all() for loc in get_locations(rid)]
    with write_conn() as conn:
        for loc in resolved:
            conn.execute(
                """INSERT INTO monitoring_locations
                   (id, region_id, name, district, latitude, longitude, elevation_m,
                    nearest_river, settlement_type, exposure, updated_at,
                    country, state_id, origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     region_id=excluded.region_id, name=excluded.name,
                     district=excluded.district, latitude=excluded.latitude,
                     longitude=excluded.longitude, nearest_river=excluded.nearest_river,
                     settlement_type=excluded.settlement_type,
                     exposure=excluded.exposure, updated_at=excluded.updated_at,
                     country=excluded.country, state_id=excluded.state_id,
                     origin=excluded.origin""",
                (
                    loc.id, loc.region_id, loc.name, loc.district,
                    loc.latitude, loc.longitude, loc.reference_elevation_m,
                    loc.nearest_river, loc.settlement_type, loc.exposure, now,
                    # A curated region id is the state slug, which is what
                    # makes the pilot regions queryable by state alongside
                    # everything resolved from the India dataset.
                    "India", loc.region_id, "curated",
                ),
            )
            n += 1
    log.info("synced %d monitoring locations to SQLite", n)
    return n


def remember_locations(rows: list[dict[str, Any]], *, region_id: str, origin: str) -> int:
    """Persist resolved locations so they survive a restart.

    Settlements are discovered from OpenStreetMap the first time a district is
    opened. Without this, ``/monitoring/loc_osm_123`` would 404 after a restart
    even though the user still has the location selected. Nothing is invented
    here - every row is a real OSM node that was actually returned.
    """
    if not rows:
        return 0
    now = iso(utcnow())
    with write_conn() as conn:
        for row in rows:
            conn.execute(
                """INSERT INTO monitoring_locations
                   (id, region_id, name, district, latitude, longitude, elevation_m,
                    nearest_river, settlement_type, exposure, updated_at,
                    country, state_id, district_id, origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     region_id=excluded.region_id, name=excluded.name,
                     district=excluded.district, latitude=excluded.latitude,
                     longitude=excluded.longitude,
                     settlement_type=excluded.settlement_type,
                     updated_at=excluded.updated_at, country=excluded.country,
                     state_id=excluded.state_id, district_id=excluded.district_id,
                     origin=excluded.origin""",
                (
                    row["id"], region_id, row["name"], row.get("district"),
                    float(row["latitude"]), float(row["longitude"]), None,
                    None, row.get("settlement_type"), None, now,
                    "India", row.get("state_id"), row.get("district_id"), origin,
                ),
            )
    return len(rows)
