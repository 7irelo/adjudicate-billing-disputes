"""Free stand-ins for the model. Oracle and null runs must behave as expected before any paid run.

`OracleLLM` answers from the gold labels, citing only what the graph actually supplied. If it can't
reach about 100%, the graders or the plumbing are broken, not the model.
"""

import json
import re
from typing import Any

from pydantic import BaseModel

from adjudicate.llm import LLMResult, Usage
from adjudicate.schemas import ClauseCitation, Decision, RecordCitation

_USAGE = Usage("fake", "fake", 0, 0, 0, 0, "end_turn")
_TYPE_BY_PREFIX = {
    "IL": "invoice_line",
    "CDR": "cdr",
    "SE": "subscription_event",
    "CO": "consent",
    "NT": "notification",
    "CN": "credit_note",
    "PY": "payment",
}


def _section(prompt: str, name: str, following: str) -> Any:
    match = re.search(rf"{name}:\n(.*?)\n\n{following}", prompt, re.DOTALL)
    return json.loads(match.group(1)) if match else []


class OracleLLM:
    def __init__(self, case: dict, required_records: list[str]) -> None:
        self.case = case
        self.required_records = required_records

    def parse(self, *, schema: type[BaseModel], user: str, **_: Any) -> LLMResult:
        clauses = {c["clause_id"]: c for c in _section(user, "CLAUSES", "RECORDS")}
        records = _section(user, "RECORDS", "<dispute>")
        by_type: dict[str, list[str]] = {}
        for record in self.required_records:
            by_type.setdefault(_TYPE_BY_PREFIX[record.split("-")[0]], []).append(record)
        citations = []
        for record_type, ids in sorted(by_type.items()):
            for evidence in records:
                if evidence["record_type"] == record_type:
                    citations.append(
                        RecordCitation(
                            handle=evidence["handle"], establishes="gold", record_ids=ids
                        )
                    )
                    break
        decision = Decision(
            decision=self.case["expected_decision"],
            credit_rule_id=self.case["expected_rule"],
            rationale="Oracle answer from gold labels.",
            clause_citations=[
                ClauseCitation(
                    clause_id=c,
                    quote=clauses[c]["text"][:60],
                    role="authorises_credit",
                )
                for c in self.case["required_clauses"]
                if c in clauses
            ],
            record_citations=citations,
            evidence_sufficient=self.case["expected_decision"] != "needs_review",
        )
        return LLMResult(parsed=decision, usage=_USAGE)


class ConstantLLM:
    """A null model: the same answer every time, with no citations. It must score poorly."""

    def __init__(self, decision: str = "charge_valid") -> None:
        self.decision = decision

    def parse(self, **_: Any) -> LLMResult:
        return LLMResult(
            parsed=Decision(decision=self.decision, rationale="Constant answer."),  # pyright: ignore[reportArgumentType]
            usage=_USAGE,
        )
