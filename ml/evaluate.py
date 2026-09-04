"""Evaluate a trained model, and compare it against the baseline heuristic.

Reports are labelled with the provenance of the data they were computed on. If
the evaluation ran on synthetic data the report says so in every section, because
a metric without its data provenance is worse than no metric at all.

Usage
-----
    python ml/evaluate.py                                   # evaluate the demo model
    python ml/evaluate.py --model ml/models/active_model.joblib --dataset data/historical/flood_events.json
    python ml/evaluate.py --baseline-only                   # score the heuristic alone
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from features import (  # noqa: E402
    FEATURE_ORDER,
    SYNTHETIC_BANNER,
    Dataset,
    load_labelled_dataset,
    make_synthetic_dataset,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services import risk_config  # noqa: E402

MODELS_DIR = Path(__file__).resolve().parent / "models"


def baseline_scores(X) -> np.ndarray:
    """The deployed weighted model's score, on the same 0-1 scale."""
    weights = np.array([risk_config.feature_weight(k) for k in FEATURE_ORDER])
    return (X.to_numpy() @ weights) / weights.sum()


def report(name: str, y_true, scores, threshold: float = 0.5) -> dict:
    preds = (scores >= threshold).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, preds, average="binary", zero_division=0
    )
    tn, fp, fn, tp = confusion_matrix(y_true, preds, labels=[0, 1]).ravel()

    metrics = {
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "average_precision": float(average_precision_score(y_true, scores)),
        "brier": float(brier_score_loss(y_true, np.clip(scores, 0, 1))),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "true_positives": int(tp), "false_positives": int(fp),
        "true_negatives": int(tn), "false_negatives": int(fn),
    }

    print(f"\n  {name}")
    print("  " + "-" * (len(name)))
    print(f"    ROC AUC            {metrics['roc_auc']:.4f}")
    print(f"    Average precision  {metrics['average_precision']:.4f}")
    print(f"    Brier score        {metrics['brier']:.4f}   (lower is better)")
    print(f"    Precision / Recall {metrics['precision']:.3f} / {metrics['recall']:.3f}   F1 {metrics['f1']:.3f}")
    print(f"    Confusion          TP {tp}  FP {fp}  TN {tn}  FN {fn}")
    if fn:
        print(f"    NOTE: {fn} missed event(s). For a warning system, recall matters "
              f"far more than precision.")
    return metrics


def load_bundle(path: Path):
    import joblib

    if not path.exists():
        raise SystemExit(
            f"No model at {path}.\nTrain one first:  python ml/train.py"
        )
    bundle = joblib.load(path)
    if not isinstance(bundle, dict) or "model" not in bundle:
        raise SystemExit(f"{path} is not a FloodSafe model bundle.")
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, default=MODELS_DIR / "demo_synthetic_model.joblib")
    parser.add_argument("--dataset", type=Path, help="Labelled evaluation dataset")
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--samples", type=int, default=3000)
    args = parser.parse_args()

    dataset: Dataset | None = load_labelled_dataset(args.dataset)
    if dataset is None:
        print("No labelled dataset available - evaluating on synthetic data instead.")
        dataset = make_synthetic_dataset(n=args.samples, seed=7)  # different seed from training

    print("\n" + "=" * 74)
    print("  FloodSafe model evaluation")
    print("=" * 74)
    print(f"  Records:   {len(dataset)}")
    print(f"  Positives: {int(dataset.y.sum())} ({dataset.y.mean():.1%})")
    print(f"  Data:      {'SYNTHETIC' if dataset.is_synthetic else 'REAL'}")

    results = {"baseline": report("Baseline weighted model (currently deployed)",
                                  dataset.y, baseline_scores(dataset.X), threshold=0.61)}

    if not args.baseline_only:
        bundle = load_bundle(args.model)
        model = bundle["model"]
        order = bundle.get("feature_order", FEATURE_ORDER)
        X = dataset.X[order]
        scores = model.predict_proba(X)[:, 1]
        results["trained"] = report(
            f"{bundle.get('model_name', 'Trained model')} "
            f"({'SYNTHETIC' if bundle.get('is_synthetic') else 'real'} training data)",
            dataset.y, scores,
        )

        delta = results["trained"]["roc_auc"] - results["baseline"]["roc_auc"]
        print(f"\n  ROC AUC difference (trained - baseline): {delta:+.4f}")
        if dataset.is_synthetic or bundle.get("is_synthetic"):
            print("  This difference is meaningless: the synthetic labels were generated")
            print("  from the baseline model itself, so the comparison is circular.")

    if dataset.is_synthetic:
        print("\n" + "!" * 74)
        for line in SYNTHETIC_BANNER.split(". "):
            print(f"  {line.strip()}")
        print("!" * 74)
        print("\n  No accuracy claim is made for FloodSafe. The deployed system uses the")
        print("  transparent baseline model, and the UI reports no accuracy figures.")

    print()


if __name__ == "__main__":
    main()
