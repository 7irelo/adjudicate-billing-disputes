"""Stage 2 of the gate: estimate P(decision correct), cross-fitted, and draw a risk-coverage curve.

The calibrator only ever narrows what stays automatic. With about 100 cases it can't support more
than about 5 features, so cross-fitting is required and intervals will be wide.
"""

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = (
    "top_clause_score",
    "clause_score_margin",
    "classifier_confidence",
    "type_agrees_with_decision_evidence",
    "evidence_completeness",
)


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
