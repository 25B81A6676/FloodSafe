"""Train a supervised flash-flood risk model.

Safety rule enforced by this script
-----------------------------------
A model trained on synthetic data is NEVER written to
``ml/models/active_model.joblib``. That path is what the running API picks up,
so allowing synthetic weights to land there would silently turn the prototype
into something that looks trained but has learned nothing real. Synthetic runs
write to ``demo_synthetic_model.joblib`` instead, and the bundle records its own
provenance.

Usage
-----
    python ml/train.py                      # synthetic smoke test of the pipeline
    python ml/train.py --dataset path.json  # train on a real labelled dataset
    python ml/train.py --dataset path.json --activate   # deploy it to the API
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    classification_report,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))

from features import (  # noqa: E402
    FEATURE_ORDER,
    SYNTHETIC_BANNER,
    Dataset,
    describe_required_dataset,
    load_labelled_dataset,
    make_synthetic_dataset,
)

MODELS_DIR = Path(__file__).resolve().parent / "models"
ACTIVE_PATH = MODELS_DIR / "active_model.joblib"
DEMO_PATH = MODELS_DIR / "demo_synthetic_model.joblib"

ESTIMATORS = {
    "random_forest": lambda: RandomForestClassifier(
        n_estimators=300, max_depth=9, min_samples_leaf=4,
        class_weight="balanced", random_state=42, n_jobs=-1,
    ),
    "gradient_boosting": lambda: GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.06, random_state=42
    ),
}


def train(dataset: Dataset, estimator_name: str = "random_forest") -> dict:
    X, y = dataset.X, dataset.y
    if y.nunique() < 2:
        raise SystemExit("Training needs both flood and non-flood examples.")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    model = ESTIMATORS[estimator_name]()
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    preds = (proba >= 0.5).astype(int)

    cv = cross_val_score(
        ESTIMATORS[estimator_name](), X, y,
        cv=StratifiedKFold(5, shuffle=True, random_state=42), scoring="roc_auc",
    )

    metrics = {
        "n_samples": int(len(X)),
        "n_positive": int(y.sum()),
        "positive_rate": round(float(y.mean()), 4),
        "accuracy": round(float(accuracy_score(y_test, preds)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, proba)), 4),
        "average_precision": round(float(average_precision_score(y_test, proba)), 4),
        "brier_score": round(float(brier_score_loss(y_test, proba)), 4),
        "cv_roc_auc_mean": round(float(cv.mean()), 4),
        "cv_roc_auc_std": round(float(cv.std()), 4),
    }

    importances = getattr(model, "feature_importances_", None)
    ranked = (
        sorted(
            zip(FEATURE_ORDER, [float(v) for v in importances]),
            key=lambda kv: kv[1], reverse=True,
        )
        if importances is not None else []
    )

    print("\n" + "=" * 74)
    print(f"  Estimator: {estimator_name}")
    print("=" * 74)
    print(f"  Samples: {metrics['n_samples']}  positives: {metrics['n_positive']} "
          f"({metrics['positive_rate']:.1%})")
    for key in ("accuracy", "roc_auc", "average_precision", "brier_score"):
        print(f"  {key:20s} {metrics[key]}")
    print(f"  {'cv_roc_auc':20s} {metrics['cv_roc_auc_mean']} +/- {metrics['cv_roc_auc_std']}")
    print("\n" + classification_report(y_test, preds, target_names=["no flood", "flood"], zero_division=0))

    if ranked:
        print("  Feature importance:")
        for name, imp in ranked:
            bar = "#" * int(round(imp * 120))
            print(f"    {name:34s} {imp:.4f} {bar}")

    if dataset.is_synthetic:
        print("\n" + "!" * 74)
        print("  " + SYNTHETIC_BANNER.replace(". ", ".\n  "))
        print("!" * 74)

    return {"model": model, "metrics": metrics, "importances": ranked}


def save(result: dict, dataset: Dataset, estimator_name: str, activate: bool) -> Path:
    import joblib

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if dataset.is_synthetic and activate:
        raise SystemExit(
            "Refusing to activate a model trained on synthetic data.\n"
            "The API would then present a model that has learned nothing real.\n"
            "Supply a genuine labelled dataset with --dataset to activate."
        )

    path = ACTIVE_PATH if activate else (DEMO_PATH if dataset.is_synthetic else MODELS_DIR / f"{estimator_name}.joblib")

    bundle = {
        "model": result["model"],
        "feature_order": FEATURE_ORDER,
        "model_id": f"{estimator_name}_risk_model",
        "model_name": f"{estimator_name.replace('_', ' ').title()} Flash-Flood Risk Model",
        "version": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "is_synthetic": dataset.is_synthetic,
        "training_data_note": dataset.source_note,
        "metrics": result["metrics"],
        "imputation": {k: 0.5 for k in FEATURE_ORDER},
    }
    joblib.dump(bundle, path)

    (MODELS_DIR / f"{path.stem}_card.json").write_text(
        json.dumps(
            {k: v for k, v in bundle.items() if k != "model"}
            | {"feature_importances": dict(result["importances"])},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  Saved model  -> {path}")
    print(f"  Saved card   -> {MODELS_DIR / (path.stem + '_card.json')}")
    if not activate:
        print("  NOT activated. The API continues to use the baseline weighted model.")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, help="Path to a labelled flood-event JSON dataset")
    parser.add_argument("--estimator", choices=sorted(ESTIMATORS), default="random_forest")
    parser.add_argument("--samples", type=int, default=3000, help="Synthetic sample count")
    parser.add_argument("--activate", action="store_true",
                        help="Write to active_model.joblib so the API uses it (real data only)")
    args = parser.parse_args()

    dataset = load_labelled_dataset(args.dataset)
    if dataset is None:
        if args.dataset:
            raise SystemExit(f"No usable dataset at {args.dataset}\n\n{describe_required_dataset()}")
        print(describe_required_dataset())
        print("\n" + "-" * 74)
        print("  Falling back to synthetic data to smoke-test the pipeline.")
        print("-" * 74)
        dataset = make_synthetic_dataset(n=args.samples)
    else:
        print(f"Loaded {len(dataset)} labelled records. {dataset.source_note}")

    result = train(dataset, args.estimator)
    save(result, dataset, args.estimator, args.activate)


if __name__ == "__main__":
    main()
