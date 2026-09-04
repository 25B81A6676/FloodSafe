"""Pluggable risk models.

The application talks to :class:`RiskModel`, never to a concrete
implementation. Today the active model is :class:`BaselineRiskModel`, a
transparent weighted sum. When a genuine labelled flood-event dataset becomes
available, a trained estimator can be dropped in behind the same interface
without touching the API layer, the map, or the dashboard.

An honest note on machine learning
----------------------------------
:class:`SklearnRiskModel` is fully implemented and will load a serialised
scikit-learn estimator from ``ml/models/`` if one exists. The prototype does not
ship a trained model, because there is no free, automatically obtainable,
labelled flash-flood event dataset for the pilot region. No accuracy figures are
invented to fill that gap.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.services import risk_config
from app.services.feature_engineering import FEATURE_KEYS, FeatureSet

log = get_logger(__name__)


@dataclass
class ModelOutput:
    score_0_100: float
    normalized: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    weight_coverage: float = 1.0
    model_id: str = "unknown"
    model_name: str = "unknown"
    model_version: str = "0"
    model_kind: str = "heuristic"
    notes: list[str] = field(default_factory=list)


class RiskModel(ABC):
    """Interface every risk model must satisfy."""

    id: str = "abstract"
    name: str = "Abstract Risk Model"
    version: str = "0"
    kind: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this model can produce predictions right now."""

    @abstractmethod
    def predict(self, features: FeatureSet) -> ModelOutput:
        """Score a feature set on the 0-100 flash-flood risk scale."""

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "available": self.is_available(),
        }


class BaselineRiskModel(RiskModel):
    """Transparent weighted sum over normalised features.

    Missing features are handled by renormalising over the weight that is
    actually available, rather than substituting zero. Substituting zero would
    quietly *lower* the risk score whenever a data source failed, which is
    exactly the wrong behaviour for a warning system.
    """

    kind = "heuristic"

    def __init__(self) -> None:
        cfg = risk_config.get_config()
        self.id = cfg.get("model_id", "baseline_weighted_v1")
        self.name = cfg.get("model_name", "Baseline Weighted Multi-Source Risk Model")
        self.version = cfg.get("version", "1.0.0")

    def is_available(self) -> bool:
        return bool(risk_config.get_config().get("features"))

    def predict(self, features: FeatureSet) -> ModelOutput:
        normalized: dict[str, float] = {}
        contributions: dict[str, float] = {}
        weights_used: dict[str, float] = {}
        notes: list[str] = []

        total_weight = 0.0
        for key in FEATURE_KEYS:
            total_weight += risk_config.feature_weight(key)

        available_weight = 0.0
        weighted_sum = 0.0

        for key in FEATURE_KEYS:
            weight = risk_config.feature_weight(key)
            if weight <= 0:
                continue
            raw = features.raw(key)
            norm = risk_config.normalize(key, raw)
            if norm is None:
                continue
            normalized[key] = round(norm, 4)
            weights_used[key] = weight
            contributions[key] = round(weight * norm, 5)
            weighted_sum += weight * norm
            available_weight += weight

        if available_weight <= 0:
            notes.append("No features were available; risk cannot be scored.")
            return ModelOutput(
                score_0_100=0.0, model_id=self.id, model_name=self.name,
                model_version=self.version, model_kind=self.kind,
                weight_coverage=0.0, notes=notes,
            )

        # Renormalise over available weight so a failed source does not
        # artificially depress the score.
        score = (weighted_sum / available_weight) * 100.0
        coverage = available_weight / total_weight if total_weight else 1.0
        if coverage < 0.999:
            notes.append(
                f"Only {coverage * 100:.0f}% of the model's feature weight was available; "
                "the score is renormalised over the features that were present."
            )

        return ModelOutput(
            score_0_100=round(max(0.0, min(100.0, score)), 1),
            normalized=normalized,
            contributions=contributions,
            weights_used=weights_used,
            weight_coverage=round(coverage, 4),
            model_id=self.id,
            model_name=self.name,
            model_version=self.version,
            model_kind=self.kind,
            notes=notes,
        )


class SklearnRiskModel(RiskModel):
    """Adapter for a serialised scikit-learn estimator.

    Expects ``ml/models/<name>.joblib`` containing a dict with keys
    ``model`` (fitted estimator exposing ``predict_proba`` or ``predict``) and
    ``feature_order`` (list of feature keys in training order).
    """

    kind = "ml"

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = model_path or (settings.project_root / "ml" / "models" / "active_model.joblib")
        self._bundle: dict[str, Any] | None = None
        self.id = "sklearn_risk_model"
        self.name = "Trained scikit-learn Risk Model"
        self.version = "unloaded"
        self._load_attempted = False

    def _load(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True
        if not self.model_path.exists():
            log.info("no trained ML model at %s - using the baseline model", self.model_path)
            return
        try:
            import joblib  # imported lazily; only needed when a model exists

            bundle = joblib.load(self.model_path)
            if not isinstance(bundle, dict) or "model" not in bundle:
                log.error("model bundle at %s has an unexpected shape", self.model_path)
                return
            self._bundle = bundle
            self.id = bundle.get("model_id", "sklearn_risk_model")
            self.name = bundle.get("model_name", "Trained scikit-learn Risk Model")
            self.version = str(bundle.get("version", "1"))
            log.info("loaded ML risk model %s v%s", self.id, self.version)
        except Exception as exc:  # noqa: BLE001 - a bad model file must not break the app
            log.error("failed to load ML model from %s: %s", self.model_path, exc)

    def is_available(self) -> bool:
        self._load()
        return self._bundle is not None

    def predict(self, features: FeatureSet) -> ModelOutput:
        if not self.is_available():
            raise RuntimeError("no trained model is loaded")
        assert self._bundle is not None

        order: list[str] = self._bundle.get("feature_order", list(FEATURE_KEYS))
        row: list[float] = []
        normalized: dict[str, float] = {}
        for key in order:
            norm = risk_config.normalize(key, features.raw(key))
            if norm is None:
                norm = float(self._bundle.get("imputation", {}).get(key, 0.0))
            normalized[key] = round(norm, 4)
            row.append(norm)

        estimator = self._bundle["model"]
        if hasattr(estimator, "predict_proba"):
            score = float(estimator.predict_proba([row])[0][1]) * 100.0
        else:
            score = float(estimator.predict([row])[0])
            if score <= 1.0:
                score *= 100.0

        contributions: dict[str, float] = {}
        importances = getattr(estimator, "feature_importances_", None)
        if importances is not None and len(importances) == len(order):
            for key, imp in zip(order, importances):
                contributions[key] = round(float(imp) * normalized.get(key, 0.0), 5)

        return ModelOutput(
            score_0_100=round(max(0.0, min(100.0, score)), 1),
            normalized=normalized,
            contributions=contributions,
            weights_used={k: float(v) for k, v in zip(order, importances)} if importances is not None else {},
            weight_coverage=1.0,
            model_id=self.id, model_name=self.name,
            model_version=self.version, model_kind=self.kind,
            notes=["Prediction produced by a trained estimator."],
        )


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
_baseline = BaselineRiskModel()
_sklearn = SklearnRiskModel()


def get_active_model() -> RiskModel:
    """Prefer a trained model when one is present, otherwise the baseline."""
    if _sklearn.is_available():
        return _sklearn
    return _baseline


def available_models() -> list[dict[str, Any]]:
    return [
        {
            **_baseline.describe(),
            "active": not _sklearn.is_available(),
            "description": (
                "Transparent weighted sum over 12 normalised multi-source features. "
                "Every weight and breakpoint is configurable in data/config/risk_weights.json."
            ),
        },
        {
            **_sklearn.describe(),
            "active": _sklearn.is_available(),
            "description": (
                "Supervised estimator loaded from ml/models/active_model.joblib. "
                "Not shipped: no free labelled flash-flood event dataset is available for "
                "the pilot region, and no accuracy figures are claimed without one."
            ),
        },
        {
            "id": "lstm_risk_model", "name": "LSTM Sequence Model", "version": "-",
            "kind": "ml", "available": False, "active": False,
            "description": (
                "Planned. A sequence model over rainfall and discharge history would "
                "capture catchment lag directly. Requires multi-year labelled events."
            ),
        },
    ]


def reset_models() -> None:
    """Rebuild model instances after a configuration reload (used by tests)."""
    global _baseline, _sklearn
    _baseline = BaselineRiskModel()
    _sklearn = SklearnRiskModel()
