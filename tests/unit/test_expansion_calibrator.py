"""Query rewriting and the stage 2 calibrator, on values worked by hand."""

import math

import pytest

from adjudicate.graph.calibrator import FEATURES, Calibrator, calibration_features
from adjudicate.retrieval.expansion import rewrite_query


def test_rewrite_keeps_the_customers_words_and_adds_policy_language() -> None:
    query = "You charged me after I cancelled"
    rewritten = rewrite_query(query)
    assert rewritten.startswith(query)
    assert "cancellation effective date" in rewritten


def test_rewrite_leaves_unknown_text_alone() -> None:
    assert rewrite_query("hello there") == "hello there"


def test_features_handle_an_empty_state() -> None:
    assert calibration_features({}) == dict.fromkeys(FEATURES, 0.0)


def test_features_worked_by_hand() -> None:
    state = {
        "clause_scores": [3.0, 1.0, 0.5],
        "classification": {"confidence": 0.8, "type_probs": {"a": 0.8, "b": 0.15, "c": 0.05}},
        "evidence": [{"record_ids": ["x"]}, {"record_ids": []}],
    }
    features = calibration_features(state)
    assert features["top_clause_score"] == 3.0
    assert features["clause_score_margin"] == 2.0
    assert features["classifier_margin"] == pytest.approx(0.65)
    assert features["evidence_completeness"] == 0.5


def test_probability_worked_by_hand() -> None:
    zero = dict.fromkeys(FEATURES, 0.0)
    calibrator = Calibrator(
        means=zero,
        scales=dict.fromkeys(FEATURES, 2.0),
        coefficients={**zero, "top_clause_score": 1.0},
        intercept=0.5,
        threshold=0.5,
    )
    # logit = 0.5 + 1.0 * (3 - 0) / 2 = 2.0
    got = calibrator.probability({**zero, "top_clause_score": 3.0})
    assert got == pytest.approx(1 / (1 + math.exp(-2.0)))
