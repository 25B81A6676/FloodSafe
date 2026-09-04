"""Region and monitoring-location registry.

Regions are pure configuration (``data/regions/*.json``). Nothing about
Uttarakhand is hard-coded in the business logic: adding another hilly region is
a matter of dropping in a new JSON file.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.db import write_conn
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


def get_region(region_id: str | None = None) -> dict[str, Any]:
    rid = region_id or settings.default_region_id
    regions = _load_all()
    if rid not in regions:
        raise RegionNotFound(rid)
    return regions[rid]


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


def get_location(location_id: str) -> Location:
    idx = _location_index()
    if location_id not in idx:
        raise LocationNotFound(location_id)
    return idx[location_id]


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

    lat0, lat1 = float(bbox["min_lat"]), float(bbox["max_lat"])
    lon0, lon1 = float(bbox["min_lon"]), float(bbox["max_lon"])
    dlat = (lat1 - lat0) / rows
    dlon = (lon1 - lon0) / cols

    cells: list[GridCell] = []
    for r in range(rows):
        for c in range(cols):
            mnlat = lat0 + r * dlat
            mnlon = lon0 + c * dlon
            cells.append(
                GridCell(
                    id=f"cell_{r}_{c}",
                    row=r,
                    col=c,
                    center_lat=round(mnlat + dlat / 2, 5),
                    center_lon=round(mnlon + dlon / 2, 5),
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
    with write_conn() as conn:
        for rid in _load_all():
            for loc in get_locations(rid):
                conn.execute(
                    """INSERT INTO monitoring_locations
                       (id, region_id, name, district, latitude, longitude, elevation_m,
                        nearest_river, settlement_type, exposure, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         region_id=excluded.region_id, name=excluded.name,
                         district=excluded.district, latitude=excluded.latitude,
                         longitude=excluded.longitude, nearest_river=excluded.nearest_river,
                         settlement_type=excluded.settlement_type,
                         exposure=excluded.exposure, updated_at=excluded.updated_at""",
                    (
                        loc.id, loc.region_id, loc.name, loc.district,
                        loc.latitude, loc.longitude, loc.reference_elevation_m,
                        loc.nearest_river, loc.settlement_type, loc.exposure, now,
                    ),
                )
                n += 1
    log.info("synced %d monitoring locations to SQLite", n)
    return n
