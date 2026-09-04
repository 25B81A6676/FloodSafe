"""Risk model configuration and feature normalisation curves.

Weights, normalisation breakpoints and class thresholds all live in
``data/config/risk_weights.json``. Nothing here is hard-coded, so the model can
be retuned without touching application code.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Sequence

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.models.enums import RiskLevel

log = get_logger(__name__)

_FALLBACK_CONFIG: dict[str, Any] = {
    "model_id": "baseline_weighted_fallback",
    "model_name": "Baseline Weighted Model (built-in fallback config)",
    "version": "0.0.0",
    "features": {},
    "risk_classes": [
        {"level": "SAFE", "min": 0, "max": 20, "color": "#16a34a", "label": "Safe"},
        {"level": "LOW", "min": 21, "max": 40, "color": "#eab308", "label": "Low"},
        {"level": "MODERATE", "min": 41, "max": 60, "color": "#f97316", "label": "Moderate"},
        {"level": "HIGH", "min": 61, "max": 80, "color": "#dc2626", "label": "High"},
        {"level": "EXTREME", "min": 81, "max": 100, "color": "#9333ea", "label": "Extreme"},
    ],
    "impact_thresholds": {"high_share": 0.18, "medium_share": 0.09},
    "confidence_rules": {
        "cached_source_factor": 0.75,
        "demo_source_factor": 0.35,
        "missing_feature_penalty": 0.06,
        "stale_after_minutes": 60,
        "levels": [
            {"level": "HIGH", "min_score": 0.8},
            {"level": "MEDIUM", "min_score": 0.55},
            {"level": "LOW", "min_score": 0.0},
        ],
    },
}


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    path = settings.config_dir / "risk_weights.json"
    if not path.exists():
        log.error("risk_weights.json not found at %s, using built-in fallback", path)
        return _FALLBACK_CONFIG
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("risk_weights.json is invalid JSON (%s), using built-in fallback", exc)
        return _FALLBACK_CONFIG

    total = sum(f.get("weight", 0.0) for f in cfg.get("features", {}).values())
    if abs(total - 1.0) > 0.02:
        log.warning("configured feature weights sum to %.3f, not 1.0 - scores are rescaled", total)
    log.info("risk model config loaded: %s v%s (%d features, weight sum %.3f)",
             cfg.get("model_id"), cfg.get("version"), len(cfg.get("features", {})), total)
    return cfg


def reload_config() -> None:
    get_config.cache_clear()
    get_config()


def feature_config(key: str) -> dict[str, Any]:
    return get_config().get("features", {}).get(key, {})


def feature_weight(key: str) -> float:
    return float(feature_config(key).get("weight", 0.0))


def interpolate(curve: Sequence[Sequence[float]], value: float) -> float:
    """Piecewise-linear interpolation, clamped at both ends.

    ``curve`` is an ordered list of ``[input, normalised_output]`` pairs.
    """
    if not curve:
        return 0.0
    pts = [(float(x), float(y)) for x, y in curve]
    if value <= pts[0][0]:
        return pts[0][1]
    if value >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= value <= x1:
            if x1 - x0 < 1e-12:
                return y1
            return y0 + (y1 - y0) * ((value - x0) / (x1 - x0))
    return pts[-1][1]


def normalize(key: str, value: float | None) -> float | None:
    """Map a raw feature value to 0-1 using its configured curve."""
    if value is None:
        return None
    cfg = feature_config(key)
    curve = cfg.get("curve")
    if not curve:
        return None
    return max(0.0, min(1.0, interpolate(curve, float(value))))


def classify(score_0_100: float) -> RiskLevel:
    """Map a 0-100 score to its risk class.

    The configured class bounds are inclusive integers (0-20, 21-40, ...), so a
    fractional score such as 40.6 would fall between two classes. Rounding
    first closes those gaps and guarantees the class always agrees with the
    integer score displayed to the user.
    """
    score = round(max(0.0, min(100.0, float(score_0_100))))
    classes = get_config().get("risk_classes", [])
    for cls in classes:
        if int(cls["min"]) <= score <= int(cls["max"]):
            return RiskLevel(cls["level"])
    # Defensive: if the configured ranges are incomplete, pick the nearest.
    if classes:
        nearest = min(classes, key=lambda c: min(abs(score - int(c["min"])), abs(score - int(c["max"]))))
        return RiskLevel(nearest["level"])
    return RiskLevel.SAFE


def risk_class_meta(level: RiskLevel | str) -> dict[str, Any]:
    lvl = level.value if isinstance(level, RiskLevel) else str(level)
    for cls in get_config().get("risk_classes", []):
        if cls["level"] == lvl:
            return cls
    return {"level": lvl, "color": "#64748b", "label": lvl.title()}


def all_risk_classes() -> list[dict[str, Any]]:
    return list(get_config().get("risk_classes", []))
