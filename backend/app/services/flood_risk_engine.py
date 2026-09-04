"""The flood risk engine.

Takes an engineered feature set, runs the active :class:`RiskModel`, and turns
the numeric output into an explainable assessment: a 0-100 score, a risk class,
a data-quality confidence rating, and a ranked list of the factors that
actually drove the result.

Explainability is not a separate narrative bolted on afterwards. The
contributor list is computed directly from the same weighted contributions that
produced the score, so the explanation cannot disagree with the number.
"""
from __future__ import annotations

from typing import Any

from app.config.logging_config import EV_RISK, get_logger
from app.models.enums import Confidence, Freshness, Impact, RiskLevel, RunMode
from app.services import risk_config, risk_models
from app.services.data_cache import iso, utcnow
from app.services.feature_engineering import FEATURE_KEYS, FeatureSet

log = get_logger(__name__)

# How much each freshness state is trusted when rating data quality.
FRESHNESS_FACTOR: dict[str, float] = {
    Freshness.LIVE.value: 1.00,
    Freshness.CACHED.value: 0.92,
    Freshness.STALE_CACHE.value: 0.75,
    Freshness.SIMULATION.value: 0.85,
    Freshness.DEMO.value: 0.35,
}

ICONS = {
    "rain": "\U0001F327",       # cloud with rain
    "forecast": "\U0001F4C5",   # calendar
    "trend": "\U0001F4C8",      # chart increasing
    "history": "\U0001F4CA",    # bar chart
    "soil": "\U0001F30A",       # water wave
    "mountain": "\U0001F3D4",   # snow-capped mountain
    "river": "\U0001F30A",      # water wave
}


def assess(
    features: FeatureSet,
    *,
    mode: RunMode = RunMode.LIVE,
    previous_score: float | None = None,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    """Produce the full explainable risk assessment for one feature set."""
    cfg = risk_config.get_config()
    model = risk_models.get_active_model()
    output = model.predict(features)

    score = output.score_0_100
    level = risk_config.classify(score)
    meta = risk_config.risk_class_meta(level)

    contributors = _contributors(features, output)
    confidence, quality_score, quality_notes = _confidence(features, output, cfg)

    trend = "UNKNOWN"
    delta = None
    if previous_score is not None:
        delta = round(score - previous_score, 1)
        if delta > 3:
            trend = "INCREASING"
        elif delta < -3:
            trend = "DECREASING"
        else:
            trend = "STEADY"

    simulated = features.any_simulated or mode == RunMode.SIMULATION
    log.info(
        "%s %s score=%.1f level=%s confidence=%s coverage=%.0f%% mode=%s",
        EV_RISK, features.location_id, score, level.value, confidence.value,
        output.weight_coverage * 100, "SIMULATION" if simulated else "LIVE",
    )

    return {
        "location_id": features.location_id,
        "risk_score": round(score),
        "risk_score_precise": score,
        "risk_level": level.value,
        "risk_label": meta.get("label", level.value.title()),
        "risk_color": meta.get("color"),
        "confidence": confidence.value,
        "data_quality_score": round(quality_score, 3),
        "data_quality_notes": quality_notes,
        "timestamp": iso(utcnow()),
        "mode": RunMode.SIMULATION.value if simulated else RunMode.LIVE.value,
        "scenario_id": scenario_id,
        "trend": trend,
        "score_delta": delta,
        "contributors": contributors,
        "primary_factors": [c for c in contributors if c["impact"] != Impact.LOW.value][:5],
        "model": {
            "id": output.model_id,
            "name": output.model_name,
            "version": output.model_version,
            "kind": output.model_kind,
            "weight_coverage": output.weight_coverage,
            "notes": output.notes,
        },
        "feature_summary": {
            "available": len(features.available_keys()),
            "total": len(FEATURE_KEYS),
            "missing": features.missing_keys(),
            "simulated": features.simulated_keys(),
            "freshness": features.freshness_summary(),
        },
        "context": features.context,
        "warnings": features.warnings[:12],
        "disclaimer": (
            "Decision-support estimate from a transparent weighted model over open "
            "environmental data. Not a validated hydrological forecast."
        ),
    }


def _contributors(features: FeatureSet, output: Any) -> list[dict[str, Any]]:
    """Rank features by their share of the weighted score."""
    cfg = risk_config.get_config()
    thresholds = cfg.get("impact_thresholds", {})
    high_share = float(thresholds.get("high_share", 0.18))
    medium_share = float(thresholds.get("medium_share", 0.09))

    total = sum(output.contributions.values()) or 1.0
    rows: list[dict[str, Any]] = []

    for key, contribution in output.contributions.items():
        fcfg = risk_config.feature_config(key)
        fv = features.features.get(key)
        share = contribution / total
        if share >= high_share:
            impact = Impact.HIGH
        elif share >= medium_share:
            impact = Impact.MEDIUM
        else:
            impact = Impact.LOW

        raw = fv.raw if fv else None
        unit = fcfg.get("unit", fv.unit if fv else "")
        rows.append(
            {
                "key": key,
                "factor": fcfg.get("label", key.replace("_", " ").title()),
                "group": fcfg.get("group", "other"),
                "impact": impact.value,
                "icon": ICONS.get(fcfg.get("icon", ""), "•"),
                "value": raw,
                "unit": unit,
                "display_value": _display_value(raw, unit),
                "normalized": output.normalized.get(key),
                "weight": output.weights_used.get(key),
                "contribution": round(contribution, 5),
                "share_pct": round(share * 100, 1),
                "source": fv.source if fv else None,
                "freshness": fv.freshness if fv else None,
                "simulated": bool(fv.simulated) if fv else False,
                "detail": (fv.note if fv and fv.note else fcfg.get("rationale")),
                "rationale": fcfg.get("rationale"),
            }
        )

    rows.sort(key=lambda r: r["contribution"], reverse=True)
    return rows


def _display_value(raw: float | None, unit: str) -> str:
    if raw is None:
        return "n/a"
    if unit == "percentile":
        return f"{raw:.0f}th pct"
    if unit == "ratio vs mean" or unit == "ratio":
        return f"{raw:.2f}x"
    if unit == "deg" or unit == "degrees":
        return f"{raw:.1f}°"
    if unit == "m":
        return f"{raw:,.0f} m"
    if abs(raw) >= 100:
        return f"{raw:,.0f} {unit}"
    return f"{raw:.1f} {unit}"


def _confidence(
    features: FeatureSet, output: Any, cfg: dict[str, Any]
) -> tuple[Confidence, float, list[str]]:
    """Rate DATA QUALITY, not flood likelihood."""
    rules = cfg.get("confidence_rules", {})
    penalty = float(rules.get("missing_feature_penalty", 0.06))
    stale_after = float(rules.get("stale_after_minutes", 60))

    factors = dict(FRESHNESS_FACTOR)
    factors[Freshness.STALE_CACHE.value] = float(rules.get("cached_source_factor", 0.75))
    factors[Freshness.DEMO.value] = float(rules.get("demo_source_factor", 0.35))

    notes: list[str] = []
    weighted, total_w = 0.0, 0.0
    for key in FEATURE_KEYS:
        fv = features.features.get(key)
        weight = output.weights_used.get(key)
        if fv is None or weight is None:
            continue
        factor = factors.get(fv.freshness, 0.5)
        weighted += weight * factor
        total_w += weight

    quality = (weighted / total_w) if total_w else 0.0

    missing = features.missing_keys()
    if missing:
        quality -= penalty * len(missing)
        notes.append(
            f"{len(missing)} of {len(FEATURE_KEYS)} features unavailable: "
            + ", ".join(risk_config.feature_config(m).get("label", m) for m in missing)
        )

    counts = features.freshness_summary()
    for state in (Freshness.DEMO.value, Freshness.STALE_CACHE.value):
        if counts.get(state):
            notes.append(f"{counts[state]} feature(s) served as {state.replace('_', ' ').lower()}.")
    if counts.get(Freshness.SIMULATION.value):
        notes.append(
            f"{counts[Freshness.SIMULATION.value]} feature(s) are simulator inputs, not measurements."
        )

    quality = max(0.0, min(1.0, quality))

    level = Confidence.LOW
    for row in rules.get("levels", []):
        if quality >= float(row.get("min_score", 0)):
            level = Confidence(row["level"])
            break

    return level, quality, notes


def score_summary(scores: list[float]) -> dict[str, Any]:
    """Aggregate helper used by the dashboard and authority views."""
    if not scores:
        return {"count": 0, "mean": None, "max": None, "min": None}
    return {
        "count": len(scores),
        "mean": round(sum(scores) / len(scores), 1),
        "max": round(max(scores), 1),
        "min": round(min(scores), 1),
    }


def distribution(levels: list[str]) -> dict[str, int]:
    dist = {lvl.value: 0 for lvl in RiskLevel}
    for lv in levels:
        if lv in dist:
            dist[lv] += 1
    return dist
