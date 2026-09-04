"""Data validation and normalisation.

Runs before anything reaches the risk engine. Its job is to reject physically
impossible values, flag suspicious ones, and record every problem it finds so
the pipeline stays inspectable instead of silently swallowing bad data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.logging_config import get_logger

log = get_logger(__name__)

# Physical plausibility bounds. Sources: world record 1-hour rainfall is about
# 305 mm (Holt, Missouri, 1947) and the 24-hour record is about 1825 mm
# (Foc-Foc, Reunion, 1966). Anything beyond these is a data error, not weather.
BOUNDS: dict[str, tuple[float, float]] = {
    "precipitation_mm_h": (0.0, 400.0),
    "rain_1h": (0.0, 400.0),
    "rain_3h": (0.0, 900.0),
    "rain_6h": (0.0, 1400.0),
    "rain_24h": (0.0, 2000.0),
    "rain_48h": (0.0, 3000.0),
    "forecast_24h": (0.0, 2000.0),
    "temperature_c": (-90.0, 60.0),
    "humidity_pct": (0.0, 100.0),
    "wind_kmh": (0.0, 500.0),
    "pressure_hpa": (300.0, 1100.0),
    "elevation_m": (-500.0, 9000.0),
    "slope_deg": (0.0, 90.0),
    "relief_m": (0.0, 8000.0),
    "river_distance_m": (0.0, 500_000.0),
    "stream_density": (0.0, 50.0),
    "discharge_m3s": (0.0, 500_000.0),
    "discharge_ratio": (0.0, 100.0),
    "rainfall_anomaly": (0.0, 100.0),
    "antecedent_precipitation_index": (0.0, 3000.0),
}


@dataclass
class ValidationReport:
    """Accumulates every issue found while normalising one observation."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def issue_count(self) -> int:
        return len(self.errors) + len(self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "repaired": self.repaired,
            "dropped_fields": self.dropped_fields,
        }


def valid_coordinates(lat: Any, lon: Any) -> bool:
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if lat_f != lat_f or lon_f != lon_f:  # NaN
        return False
    return -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0


def clean_number(
    value: Any,
    field_name: str,
    report: ValidationReport,
    *,
    default: float | None = None,
    clamp: bool = True,
) -> float | None:
    """Coerce to float, bound-check, and record what happened."""
    if value is None:
        if default is not None:
            report.repaired.append(f"{field_name}: missing, defaulted to {default}")
            return default
        report.warnings.append(f"{field_name}: missing")
        return None

    try:
        num = float(value)
    except (TypeError, ValueError):
        report.errors.append(f"{field_name}: not numeric ({value!r})")
        report.dropped_fields.append(field_name)
        return default

    if num != num or num in (float("inf"), float("-inf")):
        report.errors.append(f"{field_name}: non-finite value")
        report.dropped_fields.append(field_name)
        return default

    lo, hi = BOUNDS.get(field_name, (float("-inf"), float("inf")))
    if num < lo or num > hi:
        if clamp:
            clamped = min(max(num, lo), hi)
            report.warnings.append(
                f"{field_name}: {num:g} outside plausible range [{lo:g}, {hi:g}], clamped to {clamped:g}"
            )
            return clamped
        report.errors.append(f"{field_name}: {num:g} outside plausible range [{lo:g}, {hi:g}]")
        report.dropped_fields.append(field_name)
        return default

    return num


def check_monotonic_accumulation(acc: dict[str, float | None], report: ValidationReport) -> None:
    """Longer windows must accumulate at least as much rain as shorter ones."""
    order = ["rain_1h", "rain_3h", "rain_6h", "rain_12h", "rain_24h", "rain_48h"]
    present = [(k, acc[k]) for k in order if acc.get(k) is not None]
    for (k1, v1), (k2, v2) in zip(present, present[1:]):
        if v2 is not None and v1 is not None and v2 + 1e-6 < v1:
            report.warnings.append(
                f"accumulation not monotonic: {k2} ({v2:.1f} mm) < {k1} ({v1:.1f} mm)"
            )


def dedupe_series(points: list[dict[str, Any]], key: str = "time") -> list[dict[str, Any]]:
    """Drop duplicate timestamps, keeping the last occurrence."""
    seen: dict[Any, dict[str, Any]] = {}
    for p in points:
        seen[p.get(key)] = p
    return sorted(seen.values(), key=lambda p: str(p.get(key)))


def staleness_warning(age_minutes: float | None, limit_minutes: float, label: str) -> str | None:
    if age_minutes is None:
        return None
    if age_minutes > limit_minutes:
        return f"{label} is {age_minutes:.0f} minutes old (limit {limit_minutes:.0f})"
    return None
