"""Invariants that must hold for any input, not only the worked cases."""

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from adjudicate.credit.calculator import CreditFacts, Line, NoEligibleLines, compute_credit
from adjudicate.credit.dates import add_business_days, is_business_day
from adjudicate.credit.money import div_half_up, vat_inside
from adjudicate.credit.rules import CreditRule

ISSUED = date(2026, 6, 1)
DISPUTE = date(2026, 6, 20)


def rule(method: str, **kw) -> CreditRule:
    return CreditRule(
        rule_id=method,
        version=1,
        effective_from=date(2020, 1, 1),
        authorising_clauses=["x:y:1"],
        method=method,  # pyright: ignore[reportArgumentType]
        auto_credit_limit_cents=10**9,
        dispute_window_days=90,
        **kw,
    )


RULES = st.sampled_from(
    [
        rule("full_line_reversal"),
        rule("duplicate_reversal"),
        rule("post_cancellation_proration", proration="calendar_days"),
        rule("post_cancellation_proration", proration="thirty_day"),
        rule("post_cancellation_reversal"),
        rule("reversal_without_consent", consent_kind="oob_opt_in"),
        rule("bill_shock_over_cap", cap_cents=50000),
        rule("rerate_difference"),
    ]
)


@st.composite
def lines(draw) -> tuple[Line, ...]:
    count = draw(st.integers(1, 8))
    out = []
    for i in range(count):
        amount = draw(st.integers(0, 2_000_000))
        start = date(2026, draw(st.integers(1, 5)), 1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        out.append(
            Line(
                line_id=f"L{i}",
                line_type=draw(st.sampled_from(["recurring", "usage_oob", "usage_international"])),
                service_start=start,
                service_end=end,
                amount_cents=amount,
                issued_on=ISSUED,
                prior_credit_cents=draw(st.integers(0, amount + 1000)),
                correct_cents=draw(st.one_of(st.none(), st.integers(0, 2_000_000))),
            )
        )
    return tuple(out)


EFFECTIVE = st.one_of(st.none(), st.dates(date(2025, 12, 1), date(2026, 6, 30)))


def run(r: CreditRule, ls: tuple[Line, ...], eff: date | None):
    facts = CreditFacts(lines=ls, dispute_date=DISPUTE, cancellation_effective=eff)
    try:
        return compute_credit(r, facts)
    except NoEligibleLines:
        return None


@given(RULES, lines(), EFFECTIVE)
@settings(max_examples=300)
def test_credit_is_bounded_per_line_and_in_total(r, ls, eff) -> None:
    result = run(r, ls, eff)
    if result is None:
        return
    by_id = {ln.line_id: ln for ln in ls}
    assert result.credit_cents >= 0
    assert result.credit_cents == sum(result.allocations.values())
    for line_id, amount in result.allocations.items():
        ln = by_id[line_id]
        assert 0 <= amount <= max(0, ln.amount_cents - ln.prior_credit_cents)


@given(RULES, lines(), EFFECTIVE, st.randoms(use_true_random=False))
@settings(max_examples=200)
def test_line_order_does_not_change_the_result(r, ls, eff, rnd) -> None:
    shuffled = list(ls)
    rnd.shuffle(shuffled)
    assert run(r, ls, eff) == run(r, tuple(shuffled), eff)


@given(RULES, lines(), EFFECTIVE)
@settings(max_examples=100)
def test_runs_are_deterministic(r, ls, eff) -> None:
    assert run(r, ls, eff) == run(r, ls, eff)


@given(st.integers(0, 10_000_000), st.integers(1, 31), st.integers(1, 31))
def test_rounding_error_under_half_a_cent_per_line(amount, part, whole) -> None:
    part = min(part, whole)
    assert abs(2 * whole * div_half_up(amount * part, whole) - 2 * amount * part) <= whole


@given(st.integers(0, 10_000_000))
def test_vat_is_never_more_than_the_credit(cents) -> None:
    assert 0 <= vat_inside(cents, DISPUTE) <= cents


@given(st.dates(date(2024, 1, 1), date(2028, 12, 1)), st.integers(1, 60))
def test_notice_period_ends_on_a_business_day_after_the_request(start, count) -> None:
    end = add_business_days(start, count)
    assert end > start and is_business_day(end)
