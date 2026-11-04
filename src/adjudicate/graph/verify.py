"""Check a model decision against what was actually retrieved. Code only.

A failure sends the decision back for one repair attempt, and then to a human.
"""

import json
import re
from collections.abc import Sequence
from datetime import date

from adjudicate.credit.money import rand
from adjudicate.credit.rules import TenantPolicy
from adjudicate.schemas import Clause, Decision, EvidenceSet

_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_NUMBER = re.compile(r"(?<![\w.])R?\s?\d[\d,]*(?:\.\d+)?(?![\w])")
_ISO_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def normalise(text: str) -> str:
    return _WS.sub(" ", text.translate(_QUOTES)).strip().lower()


def _digits(token: str) -> str:
    return token.replace("R", "").replace(",", "").replace(" ", "").strip()


def allowed_numbers(
    clauses: list[Clause], evidence: list[EvidenceSet], context: Sequence[str] = ()
) -> set[str]:
    """Every number the rationale may state: from tools, clauses, or what the prompt supplied.

    `context` covers the dispute date, the period, rule windows and the customer's own text. A
    rationale may say "you claimed R1,200". It still can't reach a customer as a credit amount,
    because that figure comes from a template.
    """
    allowed: set[str] = set()
    corpus = [c.text for c in clauses] + [c.clause_id for c in clauses] + list(context)
    for ev in evidence:
        blob = json.dumps(ev.for_model(), default=str)
        corpus.append(blob)
        for value in re.findall(r'_cents": (\d+)', blob):
            cents = int(value)
            allowed.add(_digits(rand(cents)))
            allowed.add(str(cents // 100))
    for text in corpus:
        for token in _NUMBER.findall(text):
            allowed.add(_digits(token))
        for iso in _ISO_DATE.findall(text):
            year, month, day = iso.split("-")
            allowed |= {year, str(int(month)), str(int(day)), f"{int(day):02d}"}
    return allowed


def audit_numbers(
    rationale: str,
    clauses: list[Clause],
    evidence: list[EvidenceSet],
    context: Sequence[str] = (),
) -> list[str]:
    allowed = allowed_numbers(clauses, evidence, context)
    bad = []
    for token in _NUMBER.findall(rationale):
        value = _digits(token).rstrip(".")
        if value and value not in allowed and value.split(".")[0] not in allowed:
            bad.append(token.strip())
    return bad


def verify_decision(
    decision: Decision,
    clauses: list[Clause],
    evidence: list[EvidenceSet],
    policy: TenantPolicy,
    as_of: date,
    context: Sequence[str] = (),
) -> list[str]:
    """Return a list of problems. Empty means every check passed."""
    errors: list[str] = []
    by_id = {c.clause_id: c for c in clauses}
    handles = {e.handle: e for e in evidence}

    for citation in decision.clause_citations:
        clause = by_id.get(citation.clause_id)
        if clause is None:
            errors.append(f"clause {citation.clause_id} was not retrieved in this run")
        elif normalise(citation.quote) not in normalise(clause.text):
            errors.append(f"quote is not verbatim from {citation.clause_id}")
    for citation in decision.record_citations:
        if citation.handle not in handles:
            errors.append(f"evidence handle {citation.handle} does not exist in this run")
        else:
            outside = set(citation.record_ids) - set(handles[citation.handle].record_ids)
            if outside:
                errors.append(f"{sorted(outside)} are not in evidence set {citation.handle}")

    if decision.decision != "needs_review":
        if not decision.clause_citations:
            errors.append("a final decision must cite at least one clause")
        if not decision.record_citations:
            errors.append("a final decision must cite at least one record set")

    if decision.decision == "credit_due":
        if not decision.credit_rule_id:
            errors.append("credit_due needs a credit_rule_id")
        else:
            rule = policy.rule(decision.credit_rule_id, as_of)
            if rule is None:
                errors.append(f"rule {decision.credit_rule_id} is not in force for this tenant")
            elif not set(rule.authorising_clauses) & {
                c.clause_id for c in decision.clause_citations
            }:
                errors.append(
                    f"citations don't include an authorising clause for {decision.credit_rule_id}"
                )
    elif decision.credit_rule_id:
        errors.append(f"{decision.decision} must not name a credit rule")

    unsupported = audit_numbers(decision.rationale, clauses, evidence, context)
    if unsupported:
        errors.append(f"rationale states numbers not found in tool output: {unsupported}")
    return errors
