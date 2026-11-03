"""Scenario overlays: each injects one condition and records which IDs it touched.

Record gold comes from here by construction. Decision and credit gold come from the golden set,
which is written separately and never derived from the system under test.
"""

from collections.abc import Callable
from datetime import date

from adjudicate.credit.dates import notice_effective_date
from adjudicate.credit.rules import load_policy
from adjudicate.world.fraud_patterns import (
    add_diaspora_family,
    add_irsf,
    add_low_and_slow,
    add_overseas_client,
    add_pbx_burst,
    add_real_trip,
    add_wangiri,
)
from adjudicate.world.generator import (
    INTERNATIONAL_NUMBERS,
    PLANS,
    ScenarioInfo,
    World,
    month_bounds,
    months,
    sast,
)

Scenario = Callable[[World, str], ScenarioInfo]
SCENARIOS: dict[str, Scenario] = {}


def scenario(func: Scenario) -> Scenario:
    SCENARIOS[func.__name__] = func
    return func


def _start(w: World, tenant: str, name: str) -> ScenarioInfo:
    account_ref, sub = w.add_account(tenant, scenario_id=name)
    return ScenarioInfo(name, tenant, account_ref, sub)


def _plain_months(w: World, info: ScenarioInfo, last: tuple[int, int] = (2026, 5)) -> dict:
    """Bill ordinary months up to and including `last`. Returns lines per month."""
    out = {}
    for year, month in months(last=last):
        out[(year, month)] = w.bill_month(
            info.tenant, info.account_ref, info.subscription_id, year, month
        )
    return out


# A few regular contacts. Stepping through a number range is what an auto-dialler does.
def _intl_calls(
    w: World, info: ScenarioInfo, year: int, month: int, count: int, seconds: int = 300
):
    return [
        w.add_call(
            info.tenant,
            info.subscription_id,
            sast(year, month, 2 + i % 25, 10 + i % 8),
            seconds,
            f"{INTERNATIONAL_NUMBERS[0]} {1000 + (i % 3) * 137:04d}",
            "international",
        )
        for i in range(count)
    ]


@scenario
def duplicate_recurring(w: World, tenant: str) -> ScenarioInfo:
    """May's monthly fee appears twice on one invoice."""
    info = _start(w, tenant, "duplicate_recurring")
    lines = _plain_months(w, info)
    plan = PLANS[tenant]
    original = lines[(2026, 5)]
    start, end = month_bounds(2026, 5)
    dup = w.add_line(
        tenant,
        original["invoice"],
        info.subscription_id,
        "recurring",
        f"{plan['name']} monthly fee",
        start,
        end,
        plan["fee"],
    )
    info.records = {"original": [original["recurring"]], "duplicate": [dup]}
    return info


@scenario
def no_duplicate_two_months(w: World, tenant: str) -> ScenarioInfo:
    """Customer sees the same fee twice, but in two different months. Not a duplicate."""
    info = _start(w, tenant, "no_duplicate_two_months")
    lines = _plain_months(w, info)
    info.records = {
        "april": [lines[(2026, 4)]["recurring"]],
        "may": [lines[(2026, 5)]["recurring"]],
    }
    return info


@scenario
def duplicate_already_credited(w: World, tenant: str) -> ScenarioInfo:
    """A real duplicate, but a credit note already covers it in full."""
    info = _start(w, tenant, "duplicate_already_credited")
    lines = _plain_months(w, info)
    plan = PLANS[tenant]
    start, end = month_bounds(2026, 5)
    dup = w.add_line(
        tenant,
        lines[(2026, 5)]["invoice"],
        info.subscription_id,
        "recurring",
        f"{plan['name']} monthly fee",
        start,
        end,
        plan["fee"],
    )
    note = w.add_issued_credit(tenant, info.account_ref, dup, plan["fee"], date(2026, 6, 5))
    info.records = {"original": [lines[(2026, 5)]["recurring"]], "duplicate": [dup], "note": [note]}
    return info


def _cancellation(w: World, tenant: str, name: str, request: date, last: tuple[int, int]):
    """A cancellation request, its computed effective date, and billing that continues to `last`."""
    info = _start(w, tenant, name)
    policy = load_policy(tenant)
    effective = notice_effective_date(request, policy.notice["unit"], policy.notice["count"])
    request_event = w.add_event(tenant, info.subscription_id, "cancellation_request", request)
    effective_event = w.add_event(tenant, info.subscription_id, "cancellation_effective", effective)
    lines = _plain_months(w, info, last=last)
    info.records = {"request": [request_event], "effective": [effective_event]}
    return info, lines, effective


@scenario
def charged_after_cancellation(w: World, tenant: str) -> ScenarioInfo:
    """Cancelled in March. The notice period ended by mid-April, but billing ran on to May."""
    info, lines, _ = _cancellation(
        w, tenant, "charged_after_cancellation", date(2026, 3, 3), (2026, 5)
    )
    info.records["post_effective"] = [lines[(2026, 5)]["recurring"]]
    info.records["april_fee"] = [lines[(2026, 4)]["recurring"]]
    return info


@scenario
def charged_during_notice(w: World, tenant: str) -> ScenarioInfo:
    """Customer "cancelled" on 20 May, but the May fee falls inside the notice period."""
    info, lines, _ = _cancellation(w, tenant, "charged_during_notice", date(2026, 5, 20), (2026, 5))
    info.records["in_notice"] = [lines[(2026, 5)]["recurring"]]
    return info


@scenario
def cancellation_mid_month(w: World, tenant: str) -> ScenarioInfo:
    """Notice ends mid-May. Prorating tenants owe part of May; Karoo's terms keep it in full."""
    info, lines, _ = _cancellation(
        w,
        tenant,
        "cancellation_mid_month",
        date(2026, 4, 10) if tenant != "fynbos" else date(2026, 4, 15),
        (2026, 5),
    )
    info.records["partial_month"] = [lines[(2026, 5)]["recurring"]]
    return info


@scenario
def oob_no_consent(w: World, tenant: str) -> ScenarioInfo:
    """Out-of-bundle usage billed with no opt-in on record."""
    info = _start(w, tenant, "oob_no_consent")
    cdrs = [
        w.add_call(
            tenant, info.subscription_id, sast(2026, 5, 3 + i, 9), 600, "+27 21 555 0100", "oob"
        )
        for i in range(8)
    ]
    lines = _plain_months(w, info)
    info.records = {"oob_line": [lines[(2026, 5)]["usage_oob"]], "cdrs": cdrs}
    return info


@scenario
def oob_with_consent(w: World, tenant: str) -> ScenarioInfo:
    """The near twin of oob_no_consent: identical usage, but an opt-in is on record."""
    info = _start(w, tenant, "oob_with_consent")
    consent = w.add_consent(tenant, info.subscription_id, "oob_opt_in", date(2025, 6, 1))
    notice = w.add_notification(
        tenant, info.subscription_id, "bundle_depletion", sast(2026, 5, 3, 8)
    )
    for i in range(8):
        w.add_call(
            tenant, info.subscription_id, sast(2026, 5, 3 + i, 9), 600, "+27 21 555 0100", "oob"
        )
    lines = _plain_months(w, info)
    info.records = {
        "oob_line": [lines[(2026, 5)]["usage_oob"]],
        "consent": [consent],
        "notification": [notice],
    }
    return info


@scenario
def oob_partial_consent(w: World, tenant: str) -> ScenarioInfo:
    """An opt-in granted partway through May. The records can't settle which usage it covers."""
    info = _start(w, tenant, "oob_partial_consent")
    consent = w.add_consent(tenant, info.subscription_id, "oob_opt_in", date(2026, 5, 15))
    cdrs = [
        w.add_call(
            tenant, info.subscription_id, sast(2026, 5, 3 + i * 3, 9), 600, "+27 21 555 0100", "oob"
        )
        for i in range(8)
    ]
    lines = _plain_months(w, info)
    info.records = {
        "oob_line": [lines[(2026, 5)]["usage_oob"]],
        "consent": [consent],
        "cdrs": cdrs,
    }
    return info


@scenario
def roaming_no_activation(w: World, tenant: str) -> ScenarioInfo:
    info = _start(w, tenant, "roaming_no_activation")
    cdrs = [
        w.add_call(
            tenant,
            info.subscription_id,
            sast(2026, 5, 10 + i, 14),
            420,
            "+267 31 555 0100",
            "roaming",
        )
        for i in range(5)
    ]
    lines = _plain_months(w, info)
    info.records = {"roaming_line": [lines[(2026, 5)]["usage_roaming"]], "cdrs": cdrs}
    return info


@scenario
def roaming_activated(w: World, tenant: str) -> ScenarioInfo:
    """The near twin: same usage, but roaming was activated before the trip."""
    info = _start(w, tenant, "roaming_activated")
    consent = w.add_consent(tenant, info.subscription_id, "roaming_activation", date(2026, 5, 8))
    cdrs = [
        w.add_call(
            tenant,
            info.subscription_id,
            sast(2026, 5, 10 + i, 14),
            420,
            "+267 31 555 0100",
            "roaming",
        )
        for i in range(5)
    ]
    lines = _plain_months(w, info)
    line = lines[(2026, 5)]["usage_roaming"]
    info.records = {"roaming_line": [line], "consent": [consent], "cdrs": cdrs}
    return info


@scenario
def intl_valid_correct_rate(w: World, tenant: str) -> ScenarioInfo:
    """Real international calls at the right rate. Below any bill-shock threshold."""
    info = _start(w, tenant, "intl_valid_correct_rate")
    consent = w.add_consent(tenant, info.subscription_id, "international_enabled", date(2025, 6, 1))
    cdrs = _intl_calls(w, info, 2026, 5, 4, 300)
    lines = _plain_months(w, info)
    info.records = {
        "intl_line": [lines[(2026, 5)]["usage_international"]],
        "cdrs": cdrs,
        "consent": [consent],
    }
    return info


@scenario
def intl_misrated(w: World, tenant: str) -> ScenarioInfo:
    """Calls billed above the tariff in force. Only re-rating the CDRs reveals it."""
    info = _start(w, tenant, "intl_misrated")
    wrong_rate = w.rate_on(tenant, "international", date(2026, 5, 5)) + 200
    cdrs = [
        w.add_call(
            tenant,
            info.subscription_id,
            sast(2026, 5, 2 + i, 11),
            300,
            f"{INTERNATIONAL_NUMBERS[1]} {2000 + i:04d}",
            "international",
            rate_override=wrong_rate,
        )
        for i in range(4)
    ]
    lines = _plain_months(w, info)
    info.records = {"intl_line": [lines[(2026, 5)]["usage_international"]], "cdrs": cdrs}
    return info


@scenario
def tariff_changed_midperiod(w: World, tenant: str) -> ScenarioInfo:
    """Calls in March and in April, each billed at the rate in force at the time: valid."""
    info = _start(w, tenant, "tariff_changed_midperiod")
    march = _intl_calls(w, info, 2026, 3, 3)
    april = _intl_calls(w, info, 2026, 4, 3)
    lines = _plain_months(w, info)
    info.records = {
        "march_line": [lines[(2026, 3)]["usage_international"]],
        "april_line": [lines[(2026, 4)]["usage_international"]],
        "cdrs": march + april,
    }
    return info


@scenario
def bill_shock_no_sms(w: World, tenant: str) -> ScenarioInfo:
    """Large legitimate usage with no bill-shock SMS. Baobab credits above its cap; others don't."""
    info = _start(w, tenant, "bill_shock_no_sms")
    for month in (2, 3, 4):  # a heavy but consistent caller, so this isn't a fraud-style spike
        _intl_calls(w, info, 2026, month, 20, 600)
    cdrs = _intl_calls(w, info, 2026, 5, 30, 600)
    lines = _plain_months(w, info)
    info.records = {"intl_line": [lines[(2026, 5)]["usage_international"]], "cdrs": cdrs}
    return info


@scenario
def bill_shock_sms_sent(w: World, tenant: str) -> ScenarioInfo:
    """The near twin: the same usage, but the SMS was sent."""
    info = _start(w, tenant, "bill_shock_sms_sent")
    sms = w.add_notification(tenant, info.subscription_id, "bill_shock", sast(2026, 5, 12, 9))
    for month in (2, 3, 4):
        _intl_calls(w, info, 2026, month, 20, 600)
    _intl_calls(w, info, 2026, 5, 30, 600)
    lines = _plain_months(w, info)
    info.records = {"intl_line": [lines[(2026, 5)]["usage_international"]], "notification": [sms]}
    return info


@scenario
def out_of_window(w: World, tenant: str) -> ScenarioInfo:
    """A true duplicate on the December invoice, disputed in June: outside every window."""
    info = _start(w, tenant, "out_of_window")
    lines = _plain_months(w, info)
    plan = PLANS[tenant]
    start, end = month_bounds(2025, 12)
    dup = w.add_line(
        tenant,
        lines[(2025, 12)]["invoice"],
        info.subscription_id,
        "recurring",
        f"{plan['name']} monthly fee",
        start,
        end,
        plan["fee"],
    )
    info.records = {"original": [lines[(2025, 12)]["recurring"]], "duplicate": [dup]}
    return info


@scenario
def missing_records(w: World, tenant: str) -> ScenarioInfo:
    """An account with no invoice for the disputed month, so the evidence can't settle it."""
    info = _start(w, tenant, "missing_records")
    _plain_months(w, info, last=(2026, 2))
    info.records = {}
    return info


def _fraud(pattern: Callable[..., dict], name: str) -> Scenario:
    def build(w: World, tenant: str) -> ScenarioInfo:
        info = _start(w, tenant, name)
        cdrs = pattern(w, tenant, info.subscription_id)
        lines = _plain_months(w, info)
        info.records = {**cdrs, "line": [lines[(2026, 5)]["usage_international"]]}
        return info

    build.__name__ = name
    SCENARIOS[name] = build
    return build


_fraud(add_pbx_burst, "fraud_pbx_burst")
_fraud(add_irsf, "fraud_irsf")
_fraud(add_wangiri, "fraud_wangiri")
_fraud(add_low_and_slow, "fraud_low_and_slow")
_fraud(add_diaspora_family, "legit_diaspora_family")
_fraud(add_overseas_client, "legit_overseas_client")


@scenario
def legit_real_trip(w: World, tenant: str) -> ScenarioInfo:
    """A genuine trip: roaming activated, daytime calls, ordinary destinations."""
    info = _start(w, tenant, "legit_real_trip")
    consent = w.add_consent(tenant, info.subscription_id, "roaming_activation", date(2026, 5, 1))
    cdrs = add_real_trip(w, tenant, info.subscription_id)
    lines = _plain_months(w, info)
    info.records = {**cdrs, "consent": [consent], "line": [lines[(2026, 5)]["usage_roaming"]]}
    return info


def apply_all(world: World, tenants: tuple[str, ...] = ("baobab", "karoo", "fynbos")) -> None:
    for tenant in tenants:
        for name, build in SCENARIOS.items():
            world.scenarios[(tenant, name)] = build(world, tenant)
