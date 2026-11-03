"""Golden set hygiene: gold must resolve, be well-formed, and stay out of prompts."""

import json

import pytest

from adjudicate.config import REPO_ROOT
from adjudicate.ingest.pipeline import parse_corpus
from adjudicate.world.seed import build_world, manifest

CASES = [
    json.loads(line)
    for split in ("dev", "heldout")
    for line in (REPO_ROOT / f"data/golden/{split}.jsonl").read_text("utf-8").splitlines()
    if line
]
SCENARIOS = manifest(build_world(1, 0))["scenarios"]
CLAUSES = {c.clause_id for d in parse_corpus() for c in d.clauses}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_every_gold_reference_resolves(case: dict) -> None:
    scenario = SCENARIOS[f"{case['tenant']}:{case['scenario_id']}"]
    for key in case["required_records"] + case["acceptable_records"]:
        assert key in scenario["records"], f"{key} isn't recorded by {case['scenario_id']}"
    for clause in case["required_clauses"] + case["acceptable_clauses"]:
        assert clause in CLAUSES, clause
        assert clause.startswith(case["tenant"]), f"{clause} belongs to another tenant"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_case_is_internally_consistent(case: dict) -> None:
    if case["expected_decision"] == "credit_due":
        assert case["expected_rule"] and case["expected_credit_cents"] > 0
    else:
        assert case["expected_rule"] is None and case["expected_credit_cents"] == 0
    if "fraud_case" in case["tags"]:
        assert case["expected_decision"] == "needs_review"


def test_case_ids_are_unique() -> None:
    ids = [c["case_id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_golden_text_is_not_in_the_prompt() -> None:
    prompt = (REPO_ROOT / "src/adjudicate/prompts/decide.md").read_text("utf-8")
    for case in CASES:
        assert case["dispute_text"] not in prompt


def test_sizes_match_the_plan() -> None:
    assert sum(c["case_id"] < "G-0500" for c in CASES) == 100
    assert sum(c["case_id"] > "G-0500" for c in CASES) == 50


def test_every_case_declares_where_its_label_came_from() -> None:
    assert {c["provenance"] for c in CASES} <= {"assistant_draft", "hand_written"}


def test_balance_is_close_to_the_plan() -> None:
    counts = {
        d: sum(c["expected_decision"] == d for c in CASES)
        for d in ("credit_due", "charge_valid", "needs_review")
    }
    assert all(count >= 30 for count in counts.values()), counts
    assert sum("fraud_case" in c["tags"] for c in CASES) >= 20
