"""The calculator against independently worked amounts in data/golden/credit_workings.yaml."""

import ast
from datetime import date

import pytest
import yaml

from adjudicate.config import REPO_ROOT
from adjudicate.credit.calculator import (
    AmbiguousFacts,
    Consent,
    CreditFacts,
    Line,
    NoEligibleLines,
    Notification,
    compute_credit,
)
from adjudicate.credit.dates import local_date, notice_effective_date
from adjudicate.credit.rules import CreditRule

WORKINGS = yaml.safe_load(
    (REPO_ROOT / "data/golden/credit_workings.yaml").read_text(encoding="utf-8")
)


def build(case: dict) -> tuple[CreditRule, CreditFacts]:
    rule = CreditRule(
        rule_id=case["method"],
        version=1,
        effective_from=date(2020, 1, 1),
        authorising_clauses=["x:y:1.1"],
        method=case["method"],
        proration=case.get("proration"),
        consent_kind=case.get("consent_kind"),
        cap_cents=case.get("cap"),
        auto_credit_limit_cents=10**9,
        dispute_window_days=case.get("window_days", 90),
    )
    lines = tuple(
        Line(
            line_id=ln["id"],
            line_type=ln["type"],
            service_start=ln["start"],
            service_end=ln["end"],
            amount_cents=ln["amount"],
            issued_on=ln.get("issued", WORKINGS["issued"]),
            prior_credit_cents=ln.get("prior", 0),
            correct_cents=ln.get("correct"),
        )
        for ln in case["lines"]
    )
    facts = CreditFacts(
        lines=lines,
        dispute_date=case.get("dispute_date", WORKINGS["dispute_date"]),
        cancellation_effective=case.get("cancellation_effective"),
        consents=tuple(
            Consent(c["kind"], c["granted"], c.get("revoked")) for c in case.get("consents", [])
        ),
        notifications=tuple(
            Notification(n["kind"], n["sent"]) for n in case.get("notifications", [])
        ),
    )
    return rule, facts


@pytest.mark.parametrize("case", WORKINGS["cases"], ids=lambda c: c["id"])
def test_hand_worked_case(case: dict) -> None:
    rule, facts = build(case)
    expected = case["expected"]
    if expected.get("error") == "no_eligible_lines":
        with pytest.raises(NoEligibleLines):
            compute_credit(rule, facts)
    elif expected.get("error") == "ambiguous_facts":
        with pytest.raises(AmbiguousFacts):
            compute_credit(rule, facts)
    else:
        result = compute_credit(rule, facts)
        assert (result.credit_cents, result.vat_cents) == (expected["credit"], expected["vat"])
        assert sum(result.allocations.values()) == result.credit_cents


@pytest.mark.parametrize("case", WORKINGS["notice_cases"], ids=lambda c: c["id"])
def test_hand_worked_notice(case: dict) -> None:
    got = notice_effective_date(case["request"], case["unit"], case["count"])
    assert got == case["effective"]


def test_there_are_at_least_30_worked_cases() -> None:
    assert len(WORKINGS["cases"]) >= 30


def test_johannesburg_cutoff() -> None:
    from datetime import UTC, datetime

    assert local_date(datetime(2026, 3, 2, 22, 30, tzinfo=UTC)) == date(2026, 3, 3)
    assert local_date(datetime(2026, 3, 2, 21, 59, tzinfo=UTC)) == date(2026, 3, 2)


def test_no_float_in_credit_package() -> None:
    """Money stays in integers: no float literal or float() call anywhere in credit/."""
    for path in (REPO_ROOT / "src/adjudicate/credit").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            is_literal = isinstance(node, ast.Constant) and isinstance(node.value, float)
            is_call = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "float"
            )
            is_div = isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            assert not (is_literal or is_call or is_div), f"float arithmetic in {path.name}"
