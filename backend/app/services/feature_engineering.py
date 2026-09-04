"""Feature engineering: fuse the multi-source observations into model inputs.

This is the single place where raw observations from four independent
providers become the feature vector the risk model consumes. Every feature
carries its own provenance (which source produced it, how fresh it is, whether
it was simulated), so the explanation shown to the user is derived from the
same objects the score is computed from - they cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.config.logging_config import get_logger
from app.models.enums import Freshness
from app.services import historical_service

log = get_logger(__name__)

# Order matters only for presentation.
FEATURE_KEYS = (
    "rainfall_intensity",
    "rainfall_3h",
    "rainfall_24h",
    "rainfall_forecast_24h",
    "rainfall_trend",
    "rainfall_anomaly",
    "antecedent_precipitation_index",
    "slope",
    "terrain_relief",
    "river_proximity",
    "stream_density",
    "river_discharge_anomaly",
)


@dataclass
class FeatureValue:
    key: str
    raw: float | None
    unit: str
    source: str
    source_key: str
    freshness: str
    available: bool = True
    simulated: bool = False
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.raw,
            "unit": self.unit,
            "source": self.source,
            "source_key": self.source_key,
            "freshness": self.freshness,
            "available": self.available,
            "simulated": self.simulated,
            "note": self.note,
        }


@dataclass
class FeatureSet:
    location_id: str
    features: dict[str, FeatureValue] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def raw(self, key: str) -> float | None:
        fv = self.features.get(key)
        return fv.raw if fv and fv.available else None

    def available_keys(self) -> list[str]:
        return [k for k, v in self.features.items() if v.available and v.raw is not None]

    def missing_keys(self) -> list[str]:
        return [k for k in FEATURE_KEYS if k not in self.available_keys()]

    def simulated_keys(self) -> list[str]:
        return [k for k, v in self.features.items() if v.simulated]

    @property
    def any_simulated(self) -> bool:
        return any(v.simulated for v in self.features.values())

    def freshness_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for v in self.features.values():
            counts[v.freshness] = counts.get(v.freshness, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "location_id": self.location_id,
            "features": {k: v.to_dict() for k, v in self.features.items()},
            "available_count": len(self.available_keys()),
            "total_count": len(FEATURE_KEYS),
            "missing": self.missing_keys(),
            "simulated": self.simulated_keys(),
            "freshness_summary": self.freshness_summary(),
            "warnings": self.warnings,
            "context": self.context,
        }


def _fv(
    key: str, value: Any, unit: str, source: str, source_key: str, freshness: str,
    note: str | None = None,
) -> FeatureValue:
    ok = value is not None
    try:
        raw = float(value) if ok else None
    except (TypeError, ValueError):
        raw, ok = None, False
    return FeatureValue(
        key=key, raw=raw, unit=unit, source=source, source_key=source_key,
        freshness=freshness if ok else Freshness.DEMO.value,
        available=ok, note=note,
    )


def build_features(
    location_id: str,
    *,
    weather: dict[str, Any],
    terrain: dict[str, Any],
    hydrology: dict[str, Any],
    river_context: dict[str, Any],
    climatology: dict[str, Any] | None = None,
    antecedent: dict[str, Any] | None = None,
) -> FeatureSet:
    """Assemble the model feature vector from normalised source observations."""
    fs = FeatureSet(location_id=location_id)

    w_src, w_key, w_fresh = weather.get("source", "?"), weather.get("source_key", "weather"), weather.get("freshness", "DEMO")
    cur = weather.get("current") or {}
    acc = weather.get("accumulation") or {}
    fc = weather.get("forecast") or {}
    trend = weather.get("trend") or {}

    fs.features["rainfall_intensity"] = _fv(
        "rainfall_intensity", cur.get("precipitation_mm_h"), "mm/h", w_src, w_key, w_fresh
    )
    fs.features["rainfall_3h"] = _fv("rainfall_3h", acc.get("rain_3h"), "mm", w_src, w_key, w_fresh)
    fs.features["rainfall_24h"] = _fv("rainfall_24h", acc.get("rain_24h"), "mm", w_src, w_key, w_fresh)
    fs.features["rainfall_forecast_24h"] = _fv(
        "rainfall_forecast_24h", fc.get("rain_24h"), "mm", w_src, w_key, w_fresh
    )
    fs.features["rainfall_trend"] = _fv(
        "rainfall_trend", trend.get("slope_mm_per_h2"), "mm/h per h", w_src, w_key, w_fresh
    )

    # --- rainfall anomaly against the location's own ERA5 climatology --------
    anomaly = None
    anomaly_note = None
    if climatology and climatology.get("available"):
        anomaly = historical_service.percentile_of(
            acc.get("rain_24h"), {"distribution": climatology.get("_distribution")}
        )
        pcts = climatology.get("percentiles") or {}
        if anomaly is not None:
            anomaly_note = (
                f"24 h total of {acc.get('rain_24h', 0):.1f} mm sits at the {anomaly:.0f}th "
                f"percentile of {climatology.get('sample_days', 0)} comparable days "
                f"(local p95 = {pcts.get('p95', 0):.1f} mm)."
            )
    fs.features["rainfall_anomaly"] = _fv(
        "rainfall_anomaly", anomaly, "percentile",
        (climatology or {}).get("source", "ERA5 climatology"),
        (climatology or {}).get("source_key", "open-meteo-archive"),
        (climatology or {}).get("freshness", "DEMO"),
        note=anomaly_note,
    )

    # --- antecedent wetness --------------------------------------------------
    ant = antecedent or {}
    fs.features["antecedent_precipitation_index"] = _fv(
        "antecedent_precipitation_index", ant.get("api_mm"), "mm",
        ant.get("source", "Open-Meteo past-days window"),
        ant.get("source_key", "open-meteo-forecast"),
        ant.get("freshness", "DEMO"),
        note=(
            f"Catchment is {ant.get('saturation_class')} "
            f"({ant.get('total_14d_mm')} mm over the last {ant.get('window_days', 14)} days)."
            if ant.get("api_mm") is not None else None
        ),
    )

    # --- terrain -------------------------------------------------------------
    t_src, t_key, t_fresh = terrain.get("source", "?"), terrain.get("source_key", "terrain"), terrain.get("freshness", "DEMO")
    fs.features["slope"] = _fv("slope", terrain.get("slope_deg"), "deg", t_src, t_key, t_fresh,
                               note=terrain.get("method"))
    fs.features["terrain_relief"] = _fv(
        "terrain_relief", terrain.get("relief_m"), "m", t_src, t_key, t_fresh
    )

    # --- hydrological context from OSM --------------------------------------
    o_src, o_key, o_fresh = river_context.get("source", "?"), river_context.get("source_key", "overpass"), river_context.get("freshness", "DEMO")
    river_d = river_context.get("river_distance_m")
    if river_d is None and river_context.get("beyond_search_radius"):
        river_d = river_context.get("search_radius_m")
    fs.features["river_proximity"] = _fv(
        "river_proximity", river_d, "m", o_src, o_key, o_fresh,
        note=(
            f"Nearest mapped waterway: {river_context.get('nearest_waterway_name') or 'unnamed'} "
            f"({river_context.get('nearest_waterway_type') or 'watercourse'}) at "
            f"{river_d:.0f} m." if river_d is not None else None
        ),
    )
    fs.features["stream_density"] = _fv(
        "stream_density", river_context.get("stream_density_km_per_km2"),
        "km/km2", o_src, o_key, o_fresh
    )

    # --- GloFAS discharge ----------------------------------------------------
    h_src, h_key, h_fresh = hydrology.get("source", "?"), hydrology.get("source_key", "glofas"), hydrology.get("freshness", "DEMO")
    ratio = hydrology.get("discharge_ratio")
    fs.features["river_discharge_anomaly"] = _fv(
        "river_discharge_anomaly", ratio, "ratio", h_src, h_key, h_fresh,
        note=(
            f"Discharge {hydrology.get('discharge_m3s')} m3/s vs a 30-day mean of "
            f"{hydrology.get('mean_30d_m3s')} m3/s ({ratio}x)."
            if ratio is not None and hydrology.get("mean_30d_m3s") else None
        ),
    )
    if hydrology.get("freshness") == "DEMO":
        fs.features["river_discharge_anomaly"].available = False

    # --- presentation context -----------------------------------------------
    fs.context = {
        "rainfall": {
            "current_mm_h": cur.get("precipitation_mm_h"),
            "rain_1h": acc.get("rain_1h"),
            "rain_3h": acc.get("rain_3h"),
            "rain_6h": acc.get("rain_6h"),
            "rain_24h": acc.get("rain_24h"),
            "forecast_24h": fc.get("rain_24h"),
            "forecast_peak_mm_h": fc.get("max_hourly_next_24h"),
            "trend": trend.get("direction"),
        },
        "terrain": {
            "elevation_m": terrain.get("elevation_m"),
            "slope_deg": terrain.get("slope_deg"),
            "relief_m": terrain.get("relief_m"),
            "terrain_class": terrain.get("terrain_class"),
            "elevation_band": terrain.get("elevation_band"),
            "aspect": terrain.get("aspect_cardinal"),
        },
        "hydrology": {
            "river_status": hydrology.get("river_status"),
            "river_trend": hydrology.get("river_trend"),
            "discharge_m3s": hydrology.get("discharge_m3s"),
            "mean_30d_m3s": hydrology.get("mean_30d_m3s"),
            "discharge_ratio": ratio,
            "nearest_waterway": river_context.get("nearest_waterway_name"),
            "nearest_waterway_type": river_context.get("nearest_waterway_type"),
            "river_distance_m": river_context.get("river_distance_m"),
            "stream_density": river_context.get("stream_density_km_per_km2"),
        },
        "soil": {
            "api_mm": ant.get("api_mm"),
            "saturation_class": ant.get("saturation_class"),
            "total_14d_mm": ant.get("total_14d_mm"),
        },
        "climatology": {
            "available": bool(climatology and climatology.get("available")),
            "percentiles": (climatology or {}).get("percentiles"),
            "sample_days": (climatology or {}).get("sample_days"),
            "max_observed_mm": (climatology or {}).get("max_observed_mm"),
        },
    }

    for src in (weather, terrain, hydrology, river_context):
        for warn in (src.get("validation") or {}).get("warnings", []) or []:
            fs.warnings.append(warn)

    missing = fs.missing_keys()
    if missing:
        log.info("features for %s: %d/%d available, missing %s",
                 location_id, len(fs.available_keys()), len(FEATURE_KEYS), missing)
    return fs


#: Where each overridable feature also surfaces in the presentation context, so
#: the cards a user reads stay consistent with the score they are looking at.
_CONTEXT_PATHS: dict[str, tuple[str, str]] = {
    "rainfall_intensity": ("rainfall", "current_mm_h"),
    "rainfall_3h": ("rainfall", "rain_3h"),
    "rainfall_24h": ("rainfall", "rain_24h"),
    "rainfall_forecast_24h": ("rainfall", "forecast_24h"),
    "antecedent_precipitation_index": ("soil", "api_mm"),
    "river_discharge_anomaly": ("hydrology", "discharge_ratio"),
    "slope": ("terrain", "slope_deg"),
    "river_proximity": ("hydrology", "river_distance_m"),
}


def apply_overrides(fs: FeatureSet, overrides: dict[str, Any]) -> FeatureSet:
    """Replace feature values with simulator inputs, marking them SIMULATION.

    The simulator reuses the exact same feature vector and model as the live
    path; only the inputs change. Nothing about the scoring is special-cased,
    which is why a simulated result is directly comparable to a live one.

    The presentation context is updated alongside the features. Without this the
    dashboard would show a live rainfall figure next to a score computed from a
    simulated one, which reads as a bug and undermines trust in the number.
    """
    touched: list[str] = []

    for key, value in (overrides or {}).items():
        if key not in FEATURE_KEYS or value is None:
            continue
        try:
            raw = float(value)
        except (TypeError, ValueError):
            continue
        existing = fs.features.get(key)
        unit = existing.unit if existing else ""
        fs.features[key] = FeatureValue(
            key=key, raw=raw, unit=unit,
            source="Scenario simulator", source_key="simulation",
            freshness=Freshness.SIMULATION.value, available=True, simulated=True,
            note="Value supplied by the simulator, not measured.",
        )
        touched.append(key)

        path = _CONTEXT_PATHS.get(key)
        if path and isinstance(fs.context.get(path[0]), dict):
            fs.context[path[0]][path[1]] = raw

    if not touched:
        return fs

    # Recompute the derived labels that depend on overridden values.
    soil = fs.context.get("soil")
    if isinstance(soil, dict) and "antecedent_precipitation_index" in touched:
        soil["saturation_class"] = _saturation_class(soil.get("api_mm"))

    hydro = fs.context.get("hydrology")
    if isinstance(hydro, dict) and "river_discharge_anomaly" in touched:
        hydro["river_status"] = _river_status(hydro.get("discharge_ratio"))
        hydro["river_trend"] = "RISING"

    rain = fs.context.get("rainfall")
    if isinstance(rain, dict) and "rainfall_trend" in overrides:
        try:
            slope = float(overrides["rainfall_trend"])
            rain["trend"] = "RISING" if slope > 0.5 else ("FALLING" if slope < -0.5 else "STEADY")
        except (TypeError, ValueError):
            pass

    fs.context["simulated_fields"] = sorted(touched)
    return fs


def _saturation_class(api_mm: float | None) -> str:
    if api_mm is None:
        return "unknown"
    if api_mm < 10:
        return "dry"
    if api_mm < 30:
        return "slightly moist"
    if api_mm < 60:
        return "moist"
    if api_mm < 100:
        return "wet"
    return "saturated"


def _river_status(ratio: float | None) -> str:
    if ratio is None:
        return "UNKNOWN"
    if ratio >= 3.0:
        return "CRITICAL"
    if ratio >= 2.0:
        return "HIGH"
    if ratio >= 1.35:
        return "RISING"
    if ratio >= 0.8:
        return "NORMAL"
    return "LOW"
