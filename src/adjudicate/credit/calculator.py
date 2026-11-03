"""Compute a credit from a rule and facts. Pure: no I/O, no clock, no model.

The model may propose a rule. It can't choose lines or amounts: each method's predicate selects the
lines from the facts, and a rule that matches nothing raises `NoEligibleLines`.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from adjudicate.credit.money import div_half_up, rand, vat_inside
from adjudicate.credit.rules import CreditRule
from adjudicate.schemas import CreditComputation


class NoEligibleLines(Exception):
    """The rule's predicate matches no lines, so the rule can't support a credit."""


class AmbiguousFacts(Exception):
    """The records don't settle the predicate. A person has to decide."""


@dataclass(frozen=True)
class Line:
    line_id: str
    line_type: str
    service_start: date
    service_end: date
    amount_cents: int
    issued_on: date
    subscription_id: str = "S"
    description: str = ""
    prior_credit_cents: int = 0
    correct_cents: int | None = None  # the amount re-rated at the tariff in force


@dataclass(frozen=True)
class Consent:
    kind: str
    granted_on: date
    revoked_on: date | None = None


@dataclass(frozen=True)
class Notification:
    kind: str
    sent_on: date


@dataclass(frozen=True)
class CreditFacts:
    lines: tuple[Line, ...]
    dispute_date: date
    cancellation_effective: date | None = None
    consents: tuple[Consent, ...] = field(default_factory=tuple)
    notifications: tuple[Notification, ...] = field(default_factory=tuple)


@dataclass
class _Credit:
    line: Line
    gross: int
    note: str


def _remaining(line: Line) -> int:
    return max(0, line.amount_cents - line.prior_credit_cents)


def _in_window(line: Line, facts: CreditFacts, rule: CreditRule) -> bool:
    return (facts.dispute_date - line.issued_on).days <= rule.dispute_window_days


def _typed(lines: list[Line], rule: CreditRule) -> list[Line]:
    return [ln for ln in lines if not rule.line_types or ln.line_type in rule.line_types]


def _full_reversal(lines: list[Line], _f: CreditFacts, rule: CreditRule) -> list[_Credit]:
    return [_Credit(ln, ln.amount_cents, "full reversal") for ln in _typed(lines, rule)]


def _duplicates(lines: list[Line], _f: CreditFacts, rule: CreditRule) -> list[_Credit]:
    groups: dict[tuple, list[Line]] = {}
    for ln in _typed(lines, rule):
        key = (
            ln.subscription_id,
            ln.line_type,
            ln.description,
            ln.service_start,
            ln.service_end,
            ln.amount_cents,
        )
        groups.setdefault(key, []).append(ln)
    credits: list[_Credit] = []
    for group in groups.values():
        kept, *duplicates = sorted(group, key=lambda ln: ln.line_id)
        credits += [
            _Credit(ln, ln.amount_cents, f"duplicate of {kept.line_id}") for ln in duplicates
        ]
    return credits


def _after_effective(facts: CreditFacts) -> date:
    if facts.cancellation_effective is None:
        raise NoEligibleLines("no cancellation effective date on record")
    return facts.cancellation_effective


def _proration(lines: list[Line], facts: CreditFacts, rule: CreditRule) -> list[_Credit]:
    effective = _after_effective(facts)
    credits: list[_Credit] = []
    for ln in _typed(lines, rule):
        if ln.service_end <= effective:
            continue
        total_days = (ln.service_end - ln.service_start).days + 1
        if ln.service_start > effective:
            credits.append(_Credit(ln, ln.amount_cents, "all days follow the effective date"))
            continue
        used_days = (effective - ln.service_start).days + 1
        if rule.proration == "thirty_day":
            left, base = 30 - min(used_days, 30), 30
        else:
            left, base = total_days - used_days, total_days
        gross = div_half_up(ln.amount_cents * left, base)
        credits.append(_Credit(ln, gross, f"{left} of {base} days follow {effective}"))
    return credits


def _reversal_after_effective(
    lines: list[Line], facts: CreditFacts, rule: CreditRule
) -> list[_Credit]:
    effective = _after_effective(facts)
    return [
        _Credit(ln, ln.amount_cents, f"service starts after {effective}")
        for ln in _typed(lines, rule)
        if ln.service_start > effective
    ]


def _without_consent(lines: list[Line], facts: CreditFacts, rule: CreditRule) -> list[_Credit]:
    consents = [c for c in facts.consents if c.kind == rule.consent_kind]
    credits: list[_Credit] = []
    for ln in _typed(lines, rule):
        covered_all, covered_any = False, False
        for c in consents:
            last_active = (c.revoked_on - timedelta(days=1)) if c.revoked_on else date.max
            if c.granted_on <= ln.service_end and last_active >= ln.service_start:
                covered_any = True
                if c.granted_on <= ln.service_start and last_active >= ln.service_end:
                    covered_all = True
        if covered_all:
            continue
        if covered_any:
            raise AmbiguousFacts(f"{rule.consent_kind} covers only part of {ln.line_id}")
        credits.append(_Credit(ln, ln.amount_cents, f"no active {rule.consent_kind}"))
    return credits


def _bill_shock(lines: list[Line], facts: CreditFacts, rule: CreditRule) -> list[_Credit]:
    if rule.cap_cents is None:
        raise ValueError(f"rule {rule.rule_id} has no cap_cents")
    cycles: dict[tuple[int, int], list[Line]] = {}
    for ln in _typed(lines, rule):
        cycles.setdefault((ln.service_start.year, ln.service_start.month), []).append(ln)
    sent = {
        (n.sent_on.year, n.sent_on.month) for n in facts.notifications if n.kind == "bill_shock"
    }
    credits: list[_Credit] = []
    for cycle, group in sorted(cycles.items()):
        if cycle in sent:
            continue
        # Prior credits already reduced what the customer paid, so the cap applies to the net.
        over = sum(_remaining(ln) for ln in group) - rule.cap_cents
        for ln in sorted(group, key=lambda ln: ln.line_id, reverse=True):
            if over <= 0:
                break
            take = min(over, _remaining(ln))
            over -= take
            if take:
                # `over` is already net of prior credits, so add them back: the shared step nets once.
                credits.append(
                    _Credit(ln, take + ln.prior_credit_cents, f"above {rand(rule.cap_cents)} cap")
                )
    return credits


def _rerate(lines: list[Line], _f: CreditFacts, rule: CreditRule) -> list[_Credit]:
    credits: list[_Credit] = []
    for ln in _typed(lines, rule):
        if ln.correct_cents is not None and ln.amount_cents > ln.correct_cents:
            note = f"billed {rand(ln.amount_cents)}, correct rate gives {rand(ln.correct_cents)}"
            credits.append(_Credit(ln, ln.amount_cents - ln.correct_cents, note))
    return credits


_METHODS = {
    "full_line_reversal": _full_reversal,
    "duplicate_reversal": _duplicates,
    "post_cancellation_proration": _proration,
    "post_cancellation_reversal": _reversal_after_effective,
    "reversal_without_consent": _without_consent,
    "bill_shock_over_cap": _bill_shock,
    "rerate_difference": _rerate,
}


def compute_credit(rule: CreditRule, facts: CreditFacts) -> CreditComputation:
    """Apply a rule. Line order in `facts` never affects the result."""
    lines = sorted(
        (ln for ln in facts.lines if _in_window(ln, facts, rule)), key=lambda ln: ln.line_id
    )
    credits = _METHODS[rule.method](lines, facts, rule)
    if not credits:
        raise NoEligibleLines(f"{rule.rule_id} matches no lines")

    steps: list[str] = []
    allocations: dict[str, int] = {}
    gross_total = prior_total = 0
    for item in sorted(credits, key=lambda c: c.line.line_id):
        capped = min(item.gross, item.line.amount_cents)
        # Net prior credits off the entitlement, so the customer ends up where the rule says they
        # should. Capping at the line's remaining balance instead would credit the same money twice.
        applied = max(0, capped - item.line.prior_credit_cents)
        gross_total += capped
        prior_total += capped - applied
        allocations[item.line.line_id] = applied
        steps.append(
            f"{item.line.line_id}: {item.note}: {rand(capped)}, "
            f"prior credits {rand(item.line.prior_credit_cents)}, credit {rand(applied)}"
        )
    credit = sum(allocations.values())
    vat = vat_inside(credit, facts.dispute_date)
    steps.append(f"Total credit {rand(credit)}, including VAT of {rand(vat)}")
    return CreditComputation(
        rule_id=rule.rule_id,
        rule_version=rule.version,
        line_ids=sorted(allocations),
        allocations=allocations,
        gross_cents=gross_total,
        prior_credits_cents=prior_total,
        vat_cents=vat,
        credit_cents=credit,
        steps=steps,
    )
