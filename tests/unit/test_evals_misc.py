"""Calibrator fitting, judge helpers, break-even, tracing and table generation."""

import json
import random

import numpy as np
import pytest

from adjudicate.graph.calibrator import FEATURES, Calibrator
from adjudicate.ingest.parse import clauses_from_text
from adjudicate.observability import tracing
from adjudicate.schemas import Decision
from evals.breakeven import break_even_per_day
from evals.calibrate import choose_threshold, cross_fit_probabilities, fit_from_rows, risk_coverage
from evals.graders.faithfulness import (
    MUTATIONS,
    ClaimVerdict,
    Judgement,
    agreement_report,
    faithfulness_score,
    mutate,
)
from evals.judge_calibration import match_labels
from evals.make_tables import END, START, replace_block, results_block


def test_break_even_worked_by_hand() -> None:
    # $60/month against a $2 per 1k saving: 30000 disputes a month, so 1000 a day.
    assert break_even_per_day(60, 2.0) == pytest.approx(1000)
    assert break_even_per_day(60, 1.0, encoder_usd_per_1k=2.0) is None


def test_cross_fit_never_scores_a_case_with_a_model_that_saw_it() -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 2))
    y = np.array([0, 1] * 20)
    probabilities = cross_fit_probabilities(X, y, folds=4)
    assert not np.isnan(probabilities).any()
    # Pure noise can't look good when scored out of fold.
    assert abs(((probabilities > 0.5) == y).mean() - 0.5) < 0.3


def test_risk_coverage_worked_by_hand() -> None:
    curve = risk_coverage(np.array([0.9, 0.8, 0.2]), np.array([True, False, False]))
    by_threshold = {p["threshold"]: p for p in curve}
    assert by_threshold[0.2]["coverage"] == 1.0
    assert by_threshold[0.2]["risk"] == pytest.approx(2 / 3)
    assert by_threshold[0.9]["risk"] == 0.0
    assert choose_threshold(curve, 0.0)["threshold"] == 0.9  # pyright: ignore[reportOptionalSubscript]
    assert choose_threshold(curve, -1.0) is None


def row(score: float, correct: bool) -> dict:
    return {
        "features": {**dict.fromkeys(FEATURES, 0.1), "top_clause_score": score},
        "grade": {"decision_correct": correct, "credit_exact": True},
    }


def test_fit_produces_a_calibrator_the_gate_can_load() -> None:
    rows = [row(float(i), i > 10) for i in range(21)]
    fitted = fit_from_rows(rows, target_risk=0.2)
    calibrator = Calibrator(**fitted["calibrator"])
    low = calibrator.probability({**dict.fromkeys(FEATURES, 0.1), "top_clause_score": 0.0})
    high = calibrator.probability({**dict.fromkeys(FEATURES, 0.1), "top_clause_score": 20.0})
    assert low < high


def test_fit_refuses_when_every_case_has_the_same_outcome() -> None:
    with pytest.raises(ValueError):
        fit_from_rows([row(float(i), True) for i in range(10)])


def test_every_mutation_changes_the_rationale() -> None:
    decision = Decision(decision="charge_valid", rationale="The records show 4 calls in May.")
    for kind in MUTATIONS:
        assert mutate(decision, kind, random.Random(1)).rationale != decision.rationale
    assert decision.rationale == "The records show 4 calls in May."


def test_faithfulness_score_and_known_negative() -> None:
    assert faithfulness_score(Judgement(claims=[])) == 0.0
    judged = Judgement(
        claims=[
            ClaimVerdict(claim="a", verdict="supported", reason=""),
            ClaimVerdict(claim="b", verdict="unsupported", reason=""),
        ]
    )
    assert faithfulness_score(judged) == 0.5


def test_agreement_report_worked_by_hand() -> None:
    report = agreement_report(
        ["supported", "supported", "unsupported", "unsupported"],
        ["supported", "unsupported", "unsupported", "supported"],
    )
    assert report["raw_agreement"] == 0.5
    assert report["kappa"] == 0.0
    assert report["unfaithful_precision"] == 0.5
    assert report["may_gate_ci"] is False


def test_a_claim_the_judge_skipped_counts_as_unsupported() -> None:
    human = [{"case_id": "1", "claim": "there were four calls in May", "label": "supported"}]
    judged = {"1": [{"claim": "there were four calls in May", "verdict": "supported"}]}
    assert match_labels(human, judged) == (["supported"], ["supported"])
    assert match_labels(human, {"1": []}) == (["supported"], ["unsupported"])


class FakeObservation:
    def __init__(self, log, kwargs):
        self.log, self.kwargs = log, kwargs

    def end(self):
        self.log.append(("end", self.kwargs["name"]))


class FakeLangfuse:
    def __init__(self, broken: bool = False):
        self.log: list = []
        self.broken = broken

    def create_trace_id(self, seed):
        return f"trace-{seed}"

    def start_observation(self, **kwargs):
        if self.broken:
            raise RuntimeError("exporter down")
        self.log.append(("start", kwargs))
        return FakeObservation(self.log, kwargs)

    def create_score(self, **kwargs):
        if self.broken:
            raise RuntimeError("exporter down")
        self.log.append(("score", kwargs))

    def flush(self):
        if self.broken:
            raise RuntimeError("exporter down")


def test_nodes_of_one_dispute_share_a_trace(monkeypatch) -> None:
    fake = FakeLangfuse()
    monkeypatch.setattr(tracing, "_client", lambda: fake)
    state = {"tenant_id": "baobab", "dispute_id": "D-1"}
    with tracing.span("intake", state):
        pass
    with tracing.span("decide", state):
        pass
    contexts = [entry[1]["trace_context"]["trace_id"] for entry in fake.log if entry[0] == "start"]
    assert contexts == ["trace-D-1", "trace-D-1"]


def test_generation_records_usage_and_cost(monkeypatch) -> None:
    fake = FakeLangfuse()
    monkeypatch.setattr(tracing, "_client", lambda: fake)
    usage = {"served_model": "m", "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 2}
    tracing.record_generation("D-1", "baobab", usage, 0.25, "abc")
    start = next(entry[1] for entry in fake.log if entry[0] == "start")
    assert start["usage_details"]["input"] == 10
    assert start["cost_details"] == {"total": 0.25}
    assert start["metadata"]["tenant_id"] == "baobab"


def test_tracing_never_breaks_a_request(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_client", lambda: FakeLangfuse(broken=True))
    with tracing.span("decide", {"dispute_id": "D-1"}):
        ran = True
    assert ran
    tracing.record_generation("D-1", "baobab", {}, 0.0, "abc")
    tracing.push_score("D-1", "x", 1.0)
    tracing.flush()


def test_tracing_is_a_no_op_without_keys(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_client", lambda: None)
    assert tracing.trace_id_for("D-1") is None


def test_results_table_stays_empty_without_a_reportable_run(tmp_path) -> None:
    summary = {"meta": {"reportable": False}, "metrics": {}}
    (tmp_path / "run_1.json").write_text(json.dumps(summary), "utf-8")
    assert "no model results" in results_block(tmp_path)


def test_results_table_appears_for_a_reportable_run(tmp_path) -> None:
    metric = {"mean": 0.9, "ci_low": 0.8, "ci_high": 0.95, "n": 50}
    keys = [
        "decision_correct", "credit_exact", "credit_exact_given_eligible", "clause_precision",
        "clause_recall", "record_precision", "record_recall",
    ]  # fmt: skip
    summary = {
        "meta": {
            "reportable": True, "commit": "abc", "cases": 50, "decide_model": "m",
            "started": "2026-12-01T00:00:00",
        },
        "metrics": dict.fromkeys(keys, metric),
        "fraud": {},
    }  # fmt: skip
    (tmp_path / "run_1.json").write_text(json.dumps(summary), "utf-8")
    assert "0.900 | 0.800 to 0.950" in results_block(tmp_path)


def test_replace_block_touches_only_the_marked_region() -> None:
    text = f"before\n{START}\nold\n{END}\nafter"
    assert replace_block(text, "new") == f"before\n{START}\nnew\n{END}\nafter"


def test_clause_splitter_keeps_offsets_and_sub_items_together() -> None:
    text = "1. Definitions apply to these rules\n(a) first item\n(b) second item\n2. Billing happens every month here"
    clauses = clauses_from_text(text, "doc", "Title")
    assert [c.path[2] for c in clauses] == ["1", "2"]
    assert "(b) second item" in clauses[0].text
    for clause in clauses:
        assert text[clause.char_start : clause.char_end] == clause.text


def test_footnotes_are_not_clauses() -> None:
    from adjudicate.ingest.parse import _FOOTNOTE

    assert _FOOTNOTE.match("17 Rain Initial Submission 2024 Page 2 Para 1.4")
    assert _FOOTNOTE.match("94 Cell C Supplementary Submission 2024 Page 3 Para 11.2.")
    assert not _FOOTNOTE.match("6 Short title and Commencement")
    assert not _FOOTNOTE.match("3.11.11 Telkom does not envision that the opt-out")
