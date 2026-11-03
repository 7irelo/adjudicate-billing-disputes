"""Core contracts. `Decision` deliberately has no money field."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

DisputeType = Literal[
    "roaming",
    "international",
    "cancellation",
    "double_billing",
    "out_of_bundle",
    "suspected_fraud",
    "other",
]
Urgency = Literal["routine", "soon", "urgent", "critical"]
DecisionKind = Literal["credit_due", "charge_valid", "needs_review"]
Route = Literal["auto", "human", "fraud"]
RecordType = Literal[
    "cdr",
    "invoice_line",
    "subscription_event",
    "tariff_rate",
    "notification",
    "consent",
    "credit_note",
    "payment",
]


class Clause(BaseModel):
    clause_id: str
    scope: Literal["tenant", "global"]
    kind: Literal["grant", "limit", "condition", "definition", "procedure", "other"]
    path: list[str]
    text: str
    effective_from: date
    effective_to: date | None = None
    char_start: int
    char_end: int


class RetrievedClause(BaseModel):
    clause: Clause
    score: float
    rank: int


def contiguous_range(record_ids: list[str]) -> str | None:
    """'CDR-4411 to CDR-4419' when IDs share a prefix and have no gaps, otherwise None."""
    if len(record_ids) < 2:
        return None
    parts = [r.rsplit("-", 1) for r in record_ids]
    if any(len(p) != 2 or not p[1].isdigit() for p in parts) or len({p[0] for p in parts}) != 1:
        return None
    numbers = sorted(int(p[1]) for p in parts)
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return None
    prefix = parts[0][0]
    return f"{prefix}-{numbers[0]} to {prefix}-{numbers[-1]}"


class EvidenceSet(BaseModel):
    handle: str
    record_type: RecordType
    query: dict[str, Any]
    summary: dict[str, Any]
    record_ids: list[str]

    def for_model(self) -> dict[str, Any]:
        """What the model sees: aggregates, a short sample, and a range only if truly contiguous."""
        view: dict[str, Any] = {
            "handle": self.handle,
            "record_type": self.record_type,
            "summary": self.summary,
            "sample": self.record_ids[:3],
        }
        display = contiguous_range(self.record_ids)
        if display:
            view["display_range"] = display
        return view


class ClauseCitation(BaseModel):
    clause_id: str
    quote: str
    role: Literal["authorises_credit", "supports_charge", "conditions", "defines"]


class RecordCitation(BaseModel):
    handle: str
    establishes: str
    # Optional narrowing to IDs visible in the handle's summary, such as the two lines that
    # duplicate each other on an invoice. Verify rejects anything outside the handle.
    record_ids: list[str] = Field(default_factory=list)


FollowUp = Literal[
    "subscription",
    "consents",
    "notifications",
    "credit_notes",
    "payments",
    "international_cdrs",
    "roaming_cdrs",
    "oob_cdrs",
    "baseline_cdrs",
]


class Decision(BaseModel):
    decision: DecisionKind
    credit_rule_id: str | None = None
    rationale: str
    clause_citations: list[ClauseCitation] = Field(default_factory=list)
    record_citations: list[RecordCitation] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    evidence_sufficient: bool = True
    # One bounded request for more records, from a fixed list, when evidence is insufficient.
    follow_up: FollowUp | None = None


class CreditComputation(BaseModel):
    rule_id: str
    rule_version: int
    line_ids: list[str]
    allocations: dict[str, int] = Field(default_factory=dict)
    gross_cents: int
    prior_credits_cents: int
    vat_cents: int
    credit_cents: int
    steps: list[str]


class FraudSignal(BaseModel):
    name: str
    strength: Literal["weak", "strong"]
    detail: str
    record_ids: list[str] = Field(default_factory=list)


class FraudScreen(BaseModel):
    outcome: Literal["clear", "weak", "flagged"]
    signals: list[FraudSignal] = Field(default_factory=list)


class Classification(BaseModel):
    dispute_type: DisputeType
    second_type: DisputeType | None = None
    urgency: Urgency
    type_probs: dict[str, float] = Field(default_factory=dict)
    confidence: float = 0.0
    model: str = ""


class DisputeResult(BaseModel):
    dispute_id: str
    decision: Decision | None
    credit: CreditComputation | None = None
    routed_to: Route
    gate_reasons: list[str] = Field(default_factory=list)
    p_correct: float | None = None
    dispute_type: str
    urgency: str
    cited_record_ids: list[str] = Field(default_factory=list)
    cited_clause_ids: list[str] = Field(default_factory=list)
    customer_message: str = ""
    review_id: str | None = None
    resolution: dict[str, Any] | None = None
    trace_id: str | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0
