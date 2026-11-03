"""Stage 2 of the gate: P(decision correct) from observable features, loaded from a fitted file.

There's deliberately no file until a calibrator has been fitted on real model runs, so the gate
falls back to its hard rules. Fitting lives in evals/calibrate.py.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from adjudicate.config import REPO_ROOT

CALIBRATOR_PATH = REPO_ROOT / "data" / "calibrator.json"
FEATURES = (
    "top_clause_score",
    "clause_score_margin",
    "classifier_confidence",
    "classifier_margin",
    "evidence_completeness",
)


def calibration_features(state: Mapping[str, Any]) -> dict[str, float]:
    """Only what the system can observe at run time. Model self-confidence isn't one of them."""
    scores = sorted(state.get("clause_scores") or [], reverse=True)
    classification = state.get("classification") or {}
    probabilities = sorted((classification.get("type_probs") or {}).values(), reverse=True)
    evidence = state.get("evidence") or []
    return {
        "top_clause_score": float(scores[0]) if scores else 0.0,
        "clause_score_margin": float(scores[0] - scores[1]) if len(scores) > 1 else 0.0,
        "classifier_confidence": float(classification.get("confidence") or 0.0),
        "classifier_margin": float(probabilities[0] - probabilities[1])
        if len(probabilities) > 1
        else 0.0,
        "evidence_completeness": sum(1 for e in evidence if e.get("record_ids")) / len(evidence)
        if evidence
        else 0.0,
    }


@dataclass(frozen=True)
class Calibrator:
    means: dict[str, float]
    scales: dict[str, float]
    coefficients: dict[str, float]
    intercept: float
    threshold: float

    def probability(self, features: Mapping[str, float]) -> float:
        total = self.intercept
        for name in FEATURES:
            scale = self.scales[name] or 1.0
            total += self.coefficients[name] * (features[name] - self.means[name]) / scale
        return 1 / (1 + math.exp(-total))


@lru_cache
def load_calibrator() -> Calibrator | None:
    if not CALIBRATOR_PATH.exists():
        return None
    return Calibrator(**json.loads(CALIBRATOR_PATH.read_text("utf-8")))
