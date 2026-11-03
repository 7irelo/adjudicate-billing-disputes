"""Stage 2 of the gate: estimate P(decision correct), cross-fitted, and draw a risk-coverage curve.

The calibrator only ever narrows what stays automatic. With about 100 cases it can't support more
than about 5 features, so cross-fitting is required and intervals will be wide.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from adjudicate.graph.calibrator import CALIBRATOR_PATH, FEATURES


def cross_fit_probabilities(
    X: np.ndarray, y: np.ndarray, folds: int = 5, seed: int = 0
) -> np.ndarray:
    """Out-of-fold P(correct), so no case is scored by a model that saw it."""
    probabilities = np.full(len(y), np.nan)
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for train, test in splitter.split(X, y):
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=1.0))
        model.fit(X[train], y[train])
        probabilities[test] = model.predict_proba(X[test])[:, 1]
    return probabilities


def risk_coverage(probabilities: np.ndarray, correct: np.ndarray) -> list[dict[str, float]]:
    """At each threshold: the share kept automatic, and the error rate among those."""
    curve = []
    for threshold in np.unique(np.round(probabilities, 2)):
        kept = probabilities >= threshold
        if kept.any():
            curve.append(
                {
                    "threshold": float(threshold),
                    "coverage": float(kept.mean()),
                    "risk": float(1 - correct[kept].mean()),
                    "n": int(kept.sum()),
                }
            )
    return curve


def choose_threshold(curve: list[dict[str, float]], target_risk: float) -> dict[str, float] | None:
    """The highest coverage meeting the target. Chosen on dev, then confirmed on held-out."""
    meeting = [p for p in curve if p["risk"] <= target_risk]
    return max(meeting, key=lambda p: p["coverage"]) if meeting else None


def fit_from_rows(rows: list[dict], target_risk: float = 0.05, seed: int = 0) -> dict:
    """Fit on a run's rows, and report only out-of-fold quality."""
    usable = [r for r in rows if r.get("features")]
    X = np.array([[r["features"][f] for f in FEATURES] for r in usable], float)
    y = np.array(
        [r["grade"]["decision_correct"] and r["grade"]["credit_exact"] for r in usable], int
    )
    if len(set(y.tolist())) < 2:
        raise ValueError("every case has the same outcome, so there's nothing to calibrate on")
    folds = min(5, int(min(y.sum(), len(y) - y.sum())))
    probabilities = cross_fit_probabilities(X, y, folds=max(folds, 2), seed=seed)
    curve = risk_coverage(probabilities, y.astype(bool))
    chosen = choose_threshold(curve, target_risk)
    scaler = StandardScaler().fit(X)
    model = LogisticRegression(max_iter=1000, C=1.0).fit(scaler.transform(X), y)
    return {
        "n": len(y),
        "curve": curve,
        "chosen": chosen,
        "calibrator": {
            "means": dict(zip(FEATURES, np.asarray(scaler.mean_).tolist(), strict=True)),
            "scales": dict(zip(FEATURES, np.asarray(scaler.scale_).tolist(), strict=True)),
            "coefficients": dict(zip(FEATURES, model.coef_[0].tolist(), strict=True)),
            "intercept": float(model.intercept_[0]),
            "threshold": chosen["threshold"] if chosen else 1.01,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--target-risk", type=float, default=0.05)
    parser.add_argument("--write", action="store_true", help="install it for the gate to use")
    args = parser.parse_args()
    text = (args.run_dir / "results.jsonl").read_text("utf-8")
    fitted = fit_from_rows([json.loads(x) for x in text.splitlines() if x], args.target_risk)
    if args.write:
        CALIBRATOR_PATH.write_text(json.dumps(fitted["calibrator"], indent=1) + "\n", "utf-8")
    print(json.dumps({k: v for k, v in fitted.items() if k != "calibrator"}, indent=1))
