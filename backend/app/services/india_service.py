"""India administrative hierarchy: country -> state -> district -> location.

Reads ``data/india/states.json`` and ``data/india/districts.json``, both
generated from OpenStreetMap by ``scripts/build_india_geo.py``. Every bbox and
centroid in those files came from an OSM administrative relation; nothing is
hand-entered.

The important idea here is that this module does not introduce a second region
concept. It *synthesises a region dict in exactly the shape the curated
``data/regions/*.json`` files already use*, so ``risk_map_service``,
``osm_service``, ``dashboard_service`` and the risk engine consume a synthesised
state or district through the same code path they already use for Uttarakhand.
A curated region file always wins over a synthesised one, so the two pilot
regions keep their hand-checked rivers, confluences and monitoring locations.

Scope identifiers
-----------------
``india``                 national
``kerala``                state          (slug of the OSM name)
``kerala__wayanad``       district       (state slug, separator, district slug)
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from app.config.logging_config import get_logger
from app.config.settings import settings

log = get_logger(__name__)

NATIONAL_ID = "india"
DISTRICT_SEP = "__"

Scope = Literal["national", "state", "district"]


class GeographyUnavailable(RuntimeError):
    """The India dataset is missing. Run scripts/build_india_geo.py."""


class ScopeNotFound(KeyError):
    pass


# --------------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _states() -> dict[str, dict[str, Any]]:
    path = settings.india_dir / "states.json"
    if not path.exists():
        log.error("India state dataset missing at %s", path)
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {s["id"]: s for s in data.get("states", [])}


@lru_cache(maxsize=1)
def _districts() -> dict[str, dict[str, Any]]:
    path = settings.india_dir / "districts.json"
    if not path.exists():
        log.error("India district dataset missing at %s", path)
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {d["id"]: d for d in data.get("districts", [])}


@lru_cache(maxsize=1)
def _dataset_meta() -> dict[str, Any]:
    path = settings.india_dir / "states.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "generated_at": data.get("generated_at"),
        "source": data.get("source"),
        "attribution": data.get("attribution"),
    }


def reload_geography() -> None:
    for fn in (_states, _districts, _dataset_meta, _national_bbox, list_states):
        fn.cache_clear()


def available() -> bool:
    return bool(_states())


def dataset_info() -> dict[str, Any]:
    return {
        **_dataset_meta(),
        "state_count": len(_states()),
        "district_count": len(_districts()),
        "available": available(),
    }


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def list_states() -> list[dict[str, Any]]:
    districts_per_state: dict[str, int] = {}
    for district in _districts().values():
        districts_per_state[district["state_id"]] = districts_per_state.get(district["state_id"], 0) + 1
    return [
        {**state, "district_count": districts_per_state.get(state["id"], 0)}
        for state in sorted(_states().values(), key=lambda s: s["name"])
    ]


def get_state(state_id: str) -> dict[str, Any]:
    state = _states().get(state_id)
    if not state:
        raise ScopeNotFound(state_id)
    return state


def list_districts(state_id: str) -> list[dict[str, Any]]:
    get_state(state_id)  # raises if the state is unknown
    return sorted(
        (d for d in _districts().values() if d["state_id"] == state_id),
        key=lambda d: d["name"],
    )


def get_district(district_id: str) -> dict[str, Any]:
    district = _districts().get(district_id)
    if not district:
        raise ScopeNotFound(district_id)
    return district


def resolve_scope(region_id: str | None) -> tuple[Scope, dict[str, Any] | None]:
    """Classify a region id without raising for curated ids."""
    if not region_id or region_id == NATIONAL_ID:
        return "national", None
    if DISTRICT_SEP in region_id:
        return "district", get_district(region_id)
    return "state", get_state(region_id)


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _national_bbox() -> dict[str, float]:
    """Union of every state bbox - the real extent of the dataset."""
    states = list(_states().values())
    if not states:
        raise GeographyUnavailable("India dataset not generated")
    return {
        "min_lat": min(s["bbox"]["min_lat"] for s in states),
        "min_lon": min(s["bbox"]["min_lon"] for s in states),
        "max_lat": max(s["bbox"]["max_lat"] for s in states),
        "max_lon": max(s["bbox"]["max_lon"] for s in states),
    }


def national_bbox() -> dict[str, float]:
    return dict(_national_bbox())


def in_india(lat: float, lon: float) -> bool:
    """Whether a point falls inside any state's bounding box.

    An approximate land mask, and deliberately labelled as one: state bboxes are
    rectangles, so a point just offshore of a coastal state still passes. It is
    used only to stop the national risk grid from spending API calls on cells in
    the middle of the Bay of Bengal, and it is good enough for that.
    """
    for state in _states().values():
        b = state["bbox"]
        if b["min_lat"] <= lat <= b["max_lat"] and b["min_lon"] <= lon <= b["max_lon"]:
            return True
    return False


def clamp_bbox(bbox: dict[str, float], max_span_deg: float) -> dict[str, float]:
    """Shrink a bbox about its centre so no side exceeds ``max_span_deg``.

    Overpass queries are charged against a shared community endpoint, and an
    unclamped 'all hospitals and bridges in Rajasthan' query is both slow and
    rude. Callers surface the clamp in their response notes so the UI never
    implies the returned features are the complete set for the state.
    """
    lat_span = bbox["max_lat"] - bbox["min_lat"]
    lon_span = bbox["max_lon"] - bbox["min_lon"]
    if lat_span <= max_span_deg and lon_span <= max_span_deg:
        return dict(bbox)
    clat = (bbox["min_lat"] + bbox["max_lat"]) / 2
    clon = (bbox["min_lon"] + bbox["max_lon"]) / 2
    half_lat = min(lat_span, max_span_deg) / 2
    half_lon = min(lon_span, max_span_deg) / 2
    return {
        "min_lat": round(clat - half_lat, 5),
        "min_lon": round(clon - half_lon, 5),
        "max_lat": round(clat + half_lat, 5),
        "max_lon": round(clon + half_lon, 5),
    }


def was_clamped(bbox: dict[str, float], max_span_deg: float) -> bool:
    return (bbox["max_lat"] - bbox["min_lat"]) > max_span_deg or (
        bbox["max_lon"] - bbox["min_lon"]
    ) > max_span_deg


# --------------------------------------------------------------------------
# Region synthesis
# --------------------------------------------------------------------------
def _grid_for(scope: Scope) -> tuple[int, int]:
    return {
        "national": (settings.grid_rows_national, settings.grid_cols_national),
        "state": (settings.grid_rows_state, settings.grid_cols_state),
        "district": (settings.grid_rows_district, settings.grid_cols_district),
    }[scope]


def _zoom_for(scope: Scope) -> int:
    return {"national": 5, "state": 7, "district": 9}[scope]


def representative_point(state: dict[str, Any]) -> dict[str, Any]:
    """Where a state should be assessed from in the national view.

    Prefers the administrative centre the OSM relation itself designates, and
    falls back to the bounding-box centre only when a state has none. The
    distinction matters: a bbox centre is meaningless for a fragmented union
    territory, so the fallback is always labelled rather than passed off as a
    real place.
    """
    point = state.get("representative_point")
    if point and point.get("latitude") is not None:
        return point
    return {
        "latitude": state["center"]["latitude"],
        "longitude": state["center"]["longitude"],
        "name": None,
        "source": "bbox_centroid",
    }


def _representative_latlon(state: dict[str, Any]) -> tuple[float, float]:
    point = representative_point(state)
    return float(point["latitude"]), float(point["longitude"])


def _location(loc_id: str, name: str, lat: float, lon: float, **extra: Any) -> dict[str, Any]:
    return {
        "id": loc_id,
        "name": name,
        "latitude": lat,
        "longitude": lon,
        "reference_elevation_m": None,
        "nearest_river": None,
        "exposure": None,
        **extra,
    }


def synthesize_region(region_id: str | None) -> dict[str, Any]:
    """Build a region dict for a scope that has no curated file.

    The result satisfies every key the existing consumers read: ``id``,
    ``name``, ``display_name``, ``bbox``, ``center``, ``risk_grid``,
    ``gis_query_bbox`` and ``monitoring_locations``.
    """
    if not available():
        raise GeographyUnavailable(
            "India geography dataset not found. Run scripts/build_india_geo.py."
        )
    scope, node = resolve_scope(region_id)
    meta = _dataset_meta()
    common = {
        "country": "India",
        "timezone": "Asia/Kolkata",
        "synthesized": True,
        "scope": scope,
        "geography_source": meta.get("source"),
        "geography_generated_at": meta.get("generated_at"),
        "attribution": {"boundaries": meta.get("attribution")},
    }
    rows, cols = _grid_for(scope)

    if scope == "national":
        bbox = national_bbox()
        return {
            **common,
            "id": NATIONAL_ID,
            "name": "India",
            "display_name": "India",
            "scope": "national",
            "terrain_type": "All terrain types - Himalaya, Indo-Gangetic plain, Deccan plateau, Western and Eastern Ghats, coastal plains",
            "description": (
                f"National view over {len(_states())} states and union territories. "
                "Grid cells are coarse by design: each is an independent assessment at "
                "its own centre point, not a claim about every square kilometre it covers."
            ),
            "bbox": bbox,
            "center": {"latitude": 22.5, "longitude": 79.0},
            "default_zoom": _zoom_for("national"),
            "risk_grid": {"rows": rows, "cols": cols, "bbox": bbox, "mask": "state_bbox"},
            "gis_query_bbox": clamp_bbox(bbox, settings.gis_max_bbox_degrees),
            # Labelled by what the point actually IS, so a fallback centroid is
            # never mistaken for a real administrative centre.
            "monitoring_locations": [
                _location(
                    f"loc_state_{state['id']}",
                    state["name"],
                    *_representative_latlon(state),
                    district=None,
                    state_id=state["id"],
                    settlement_type=(
                        "state_admin_centre"
                        if representative_point(state)["source"] == "osm_admin_centre"
                        else "state_centroid"
                    ),
                    assessed_at=representative_point(state).get("name"),
                )
                for state in list_states()
            ],
        }

    if scope == "state":
        assert node is not None
        districts = list_districts(node["id"])
        return {
            **common,
            "id": node["id"],
            "name": node["name"],
            "display_name": f"{node['name']}, India",
            "iso_code": node.get("iso_code"),
            "state_type": node.get("type"),
            "terrain_type": None,
            "description": (
                f"{node['name']} - {len(districts)} districts. Boundaries from "
                "OpenStreetMap; risk is computed per grid cell from the same "
                "multi-source engine used everywhere else in the system."
            ),
            "bbox": node["bbox"],
            "center": node["center"],
            "default_zoom": _zoom_for("state"),
            "risk_grid": {"rows": rows, "cols": cols, "bbox": node["bbox"]},
            "gis_query_bbox": clamp_bbox(node["bbox"], settings.gis_max_bbox_degrees),
            "monitoring_locations": [
                _location(
                    f"loc_{d['id']}",
                    d["name"],
                    d["center"]["latitude"],
                    d["center"]["longitude"],
                    district=d["name"],
                    district_id=d["id"],
                    state_id=node["id"],
                    settlement_type="district_centroid",
                )
                for d in districts
            ],
        }

    assert node is not None
    state = get_state(node["state_id"])
    return {
        **common,
        "id": node["id"],
        "name": node["name"],
        "display_name": f"{node['name']}, {state['name']}",
        "state_id": state["id"],
        "state_name": state["name"],
        "terrain_type": None,
        "description": (
            f"{node['name']} district, {state['name']}. Boundaries from OpenStreetMap. "
            "Settlements inside the district are resolved from OpenStreetMap on demand."
        ),
        "bbox": node["bbox"],
        "center": node["center"],
        "default_zoom": _zoom_for("district"),
        "risk_grid": {"rows": rows, "cols": cols, "bbox": node["bbox"]},
        "gis_query_bbox": clamp_bbox(node["bbox"], settings.gis_max_bbox_degrees),
        "monitoring_locations": [
            _location(
                f"loc_{node['id']}",
                f"{node['name']} district centre",
                node["center"]["latitude"],
                node["center"]["longitude"],
                district=node["name"],
                district_id=node["id"],
                state_id=state["id"],
                settlement_type="district_centroid",
            )
        ],
    }
