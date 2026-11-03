"""Stage 1 of the confidence gate: hard rules. Any reason sends the dispute to a person.

The stage 2 calibrator (evals/calibrate.py) only ever narrows what stays automatic. It can't move
a dispute out of human review.
"""

from dataclasses import dataclass

from adjudicate.schemas import CreditComputation, Decision, FraudScreen


@dataclass(frozen=True)
class GateInput:
    decision: Decision | None
    credit: CreditComputation | None
    auto_credit_limit_cents: int | None
    fraud: FraudScreen | None
    verify_errors: list[str]
    llm_failure: str | None
    credit_status: str | None
    injection_suspected: bool
    evidence_empty: bool
    no_clauses: bool
    fallback_served: bool
    auto_finalise_rejections: bool


def gate_reasons(g: GateInput) -> list[str]:
    reasons: list[str] = []
    if g.fraud and g.fraud.outcome != "clear":
        reasons.append(f"fraud_signal_{g.fraud.outcome}")
    if g.llm_failure:
        reasons.append(f"llm_{g.llm_failure}")
    if g.verify_errors:
        reasons.append("verify_failed")
    if g.fallback_served:
        reasons.append("fallback_model_served")
    if g.injection_suspected:
        reasons.append("injection_heuristic")
    if g.evidence_empty:
        reasons.append("records_missing")
    if g.no_clauses:
        reasons.append("no_clauses_retrieved")
    if g.credit_status and g.credit_status != "ok":
        reasons.append(f"credit_{g.credit_status}")

    decision = g.decision
    if decision is None:
        reasons.append("no_decision")
    else:
        if decision.decision == "needs_review":
            reasons.append("model_needs_review")
        if decision.missing_facts:
            reasons.append("missing_facts")
        if not decision.evidence_sufficient:
            reasons.append("evidence_insufficient")
        if decision.decision == "charge_valid" and not g.auto_finalise_rejections:
            reasons.append("adverse_outcome_needs_human")
    if g.credit is not None:
        if (
            g.auto_credit_limit_cents is not None
            and g.credit.credit_cents > g.auto_credit_limit_cents
        ):
            reasons.append("credit_above_auto_limit")
        if g.credit.credit_cents == 0:
            reasons.append("credit_is_zero")
    return reasons
