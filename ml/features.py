"""Feature engineering for supervised flash-flood modelling.

This module deliberately reuses the *same* feature definitions and
normalisation curves as the running application (``data/config/risk_weights.json``).
A model trained here therefore consumes exactly the features the API produces
at inference time, which removes the most common source of train/serve skew.

Nothing here invents flood observations. ``load_labelled_dataset`` reads a
user-supplied, cited dataset; if none exists it says so and returns nothing.
``make_synthetic_dataset`` exists only to prove the pipeline executes, and
every row it produces is stamped ``SYNTHETIC DEMONSTRATION DATA``.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = PROJECT_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import risk_config  # noqa: E402
from app.services.feature_engineering import FEATURE_KEYS  # noqa: E402

#: Canonical column order. Training and inference must agree on this.
FEATURE_ORDER: list[str] = list(FEATURE_KEYS)

TARGET = "flood_occurred"

DATASET_PATH = PROJECT_ROOT / "data" / "historical" / "flood_events.json"

REQUIRED_SCHEMA = {
    "event_id": "unique string identifier",
    "date": "ISO-8601 date of the observation",
    "latitude": "float, WGS84",
    "longitude": "float, WGS84",
    "region_id": "string matching a file in data/regions/",
    "flood_occurred": "0 or 1 - did a flash flood occur in the following 24 h",
    "severity": "optional: minor | moderate | major",
    "source": "citation for the record (agency, report, DOI)",
}


@dataclass
class Dataset:
    X: pd.DataFrame
    y: pd.Series
    meta: pd.DataFrame
    is_synthetic: bool
    source_note: str

    def __len__(self) -> int:
        return len(self.X)


# --------------------------------------------------------------------------
# Feature construction
# --------------------------------------------------------------------------
def normalise_row(raw: dict[str, float | None]) -> dict[str, float]:
    """Apply the application's own normalisation curves to a raw feature dict.

    Missing values become the neutral midpoint of the curve rather than zero,
    so absent data does not read as 'safe'.
    """
    out: dict[str, float] = {}
    for key in FEATURE_ORDER:
        value = raw.get(key)
        norm = risk_config.normalize(key, value)
        if norm is None:
            curve = risk_config.feature_config(key).get("curve") or [[0, 0], [1, 1]]
            norm = float(np.median([p[1] for p in curve]))
        out[key] = float(norm)
    return out


def build_matrix(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Turn raw observation dicts into a normalised feature matrix."""
    return pd.DataFrame(
        [normalise_row(r) for r in rows], columns=FEATURE_ORDER
    ).astype(float)


def feature_documentation() -> pd.DataFrame:
    """Human-readable description of every model input."""
    cfg = risk_config.get_config()
    return pd.DataFrame(
        [
            {
                "feature": key,
                "label": f.get("label"),
                "group": f.get("group"),
                "unit": f.get("unit"),
                "baseline_weight": f.get("weight"),
                "direction": f.get("direction"),
                "rationale": f.get("rationale"),
            }
            for key, f in cfg.get("features", {}).items()
        ]
    )


# --------------------------------------------------------------------------
# Real labelled data
# --------------------------------------------------------------------------
def load_labelled_dataset(path: Path | None = None) -> Dataset | None:
    """Load a genuine, cited flash-flood event dataset if the operator has one.

    Returns ``None`` when no dataset is present. It never substitutes synthetic
    rows silently.
    """
    target = path or DATASET_PATH
    if not target.exists():
        return None

    records = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        return None

    missing = [r for r in records if TARGET not in r]
    if missing:
        raise ValueError(
            f"{len(missing)} record(s) lack the '{TARGET}' label. "
            f"Required schema: {json.dumps(REQUIRED_SCHEMA, indent=2)}"
        )

    X = build_matrix(records)
    y = pd.Series([int(r[TARGET]) for r in records], name=TARGET)
    meta = pd.DataFrame(
        [
            {k: r.get(k) for k in ("event_id", "date", "latitude", "longitude", "region_id", "source")}
            for r in records
        ]
    )
    return Dataset(
        X=X, y=y, meta=meta, is_synthetic=False,
        source_note=f"Operator-supplied labelled dataset: {target}",
    )


# --------------------------------------------------------------------------
# Synthetic data - pipeline demonstration only
# --------------------------------------------------------------------------
SYNTHETIC_BANNER = (
    "SYNTHETIC DEMONSTRATION DATA - generated from the baseline weighted model "
    "plus noise. It contains no real flood observations. Metrics computed on it "
    "measure only whether the pipeline runs; they say NOTHING about real-world "
    "predictive skill and must never be quoted as model accuracy."
)


def make_synthetic_dataset(n: int = 3000, seed: int = 42) -> Dataset:
    """Generate a labelled dataset for smoke-testing the training pipeline.

    Labels are produced by thresholding the baseline weighted score with added
    noise. A model trained on this can only rediscover the baseline heuristic -
    which is exactly why its scores are not evidence of anything.
    """
    rng = np.random.default_rng(seed)

    raw_rows: list[dict[str, Any]] = []
    for _ in range(n):
        wet = rng.random() < 0.35  # a wet-season-like subset
        intensity = float(rng.gamma(1.4, 9.0) if wet else rng.gamma(0.7, 2.0))
        raw_rows.append(
            {
                "rainfall_intensity": intensity,
                "rainfall_3h": intensity * float(rng.uniform(1.2, 3.2)),
                "rainfall_24h": intensity * float(rng.uniform(3.0, 9.0)),
                "rainfall_forecast_24h": intensity * float(rng.uniform(1.0, 6.0)),
                "rainfall_trend": float(rng.normal(1.5 if wet else -0.2, 3.0)),
                "rainfall_anomaly": float(np.clip(rng.normal(75 if wet else 40, 20), 0, 100)),
                "antecedent_precipitation_index": float(
                    np.clip(rng.normal(70 if wet else 18, 30), 0, 200)
                ),
                "slope": float(np.clip(rng.normal(20, 9), 0, 60)),
                "terrain_relief": float(np.clip(rng.normal(500, 260), 0, 2000)),
                "river_proximity": float(np.clip(rng.exponential(700), 0, 6000)),
                "stream_density": float(np.clip(rng.normal(1.3, 0.7), 0, 6)),
                "river_discharge_anomaly": float(
                    np.clip(rng.normal(1.9 if wet else 1.0, 0.7), 0.2, 6.0)
                ),
            }
        )

    X = build_matrix(raw_rows)

    weights = np.array([risk_config.feature_weight(k) for k in FEATURE_ORDER])
    score = (X.to_numpy() @ weights) / weights.sum()
    noise = rng.normal(0, 0.09, size=len(score))
    y = pd.Series(((score + noise) > 0.62).astype(int), name=TARGET)

    meta = pd.DataFrame(
        {
            "event_id": [f"synthetic_{i:05d}" for i in range(len(X))],
            "source": SYNTHETIC_BANNER,
        }
    )
    return Dataset(X=X, y=y, meta=meta, is_synthetic=True, source_note=SYNTHETIC_BANNER)


def describe_required_dataset() -> str:
    return (
        "No labelled flash-flood dataset is bundled with this prototype.\n\n"
        f"To enable supervised training, place a JSON array at:\n  {DATASET_PATH}\n\n"
        "Each record must contain:\n"
        + "\n".join(f"  {k:16s} {v}" for k, v in REQUIRED_SCHEMA.items())
        + "\n\nCandidate sources to compile such a dataset:\n"
        "  - India Meteorological Department rainfall and cloudburst bulletins\n"
        "  - Uttarakhand / Himachal State Disaster Management Authority incident reports\n"
        "  - Central Water Commission flood forecasting station records\n"
        "  - EM-DAT and the Dartmouth Flood Observatory global flood archives\n"
        "  - Peer-reviewed case studies of specific events\n\n"
        "Every record should carry a citation in its 'source' field. Do not\n"
        "populate this file with estimated or generated events."
    )


if __name__ == "__main__":
    print(f"Feature order ({len(FEATURE_ORDER)}):")
    for i, k in enumerate(FEATURE_ORDER, 1):
        print(f"  {i:2d}. {k}")
    print()
    real = load_labelled_dataset()
    if real is None:
        print(describe_required_dataset())
    else:
        print(f"Loaded {len(real)} labelled records from {DATASET_PATH}")
