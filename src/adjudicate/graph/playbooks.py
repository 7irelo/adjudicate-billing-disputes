"""A fixed, testable evidence plan per dispute type. Tools are called by code, not by the model."""

from dataclasses import dataclass
from datetime import date, timedelta

from adjudicate.graph.intake import default_period
from adjudicate.mcp_server.service import Tools
from adjudicate.schemas import EvidenceSet

_INTL = ["international", "premium"]


@dataclass(frozen=True)
class Playbook:
    clause_queries: tuple[str, ...]
    records: tuple[str, ...]
    lookback_days: int = 0  # widen the invoice search when the period wasn't stated


COMMON_QUERIES = ("credit limit amount already credited query dispute window after invoice",)

PLAYBOOKS: dict[str, Playbook] = {
    "roaming": Playbook(
        ("roaming charges activation reversal", "roaming not activated charge credit"),
        ("invoice", "roaming_cdrs", "consents", "notifications", "credit_notes", "tariff_roaming"),
    ),
    "international": Playbook(
        (
            "international calls rate per minute payable",
            "usage rated above tariff credit difference overcharge",
            "bill shock threshold usage charges SMS credit",
        ),
        (
            "invoice",
            "international_cdrs",
            "baseline_cdrs",
            "consents",
            "notifications",
            "credit_notes",
            "tariff_international",
        ),
    ),
    "cancellation": Playbook(
        (
            "cancel notice period business days effective cancellation date",
            "recurring charge after effective cancellation date credit reversal",
            "charges during notice period remain payable",
        ),
        ("invoice", "subscription", "payments", "credit_notes"),
        lookback_days=150,
    ),
    "double_billing": Playbook(
        ("same charge billed twice duplicate credit", "duplicate charge service period"),
        ("invoice", "payments", "credit_notes"),
    ),
    "out_of_bundle": Playbook(
        (
            "out-of-bundle usage opt-in consent charge",
            "out-of-bundle charged without opt-in reversal",
            "bundle depletion notification SMS",
        ),
        ("invoice", "oob_cdrs", "consents", "notifications", "credit_notes", "tariff_oob"),
    ),
    "suspected_fraud": Playbook(
        (
            "suspected fraud unauthorised use investigation",
            "customer liability compromise equipment charges payable",
        ),
        ("invoice", "international_cdrs", "baseline_cdrs", "consents", "credit_notes"),
    ),
    "other": Playbook(
        ("billing query charge credit", "credit limit approval"),
        ("invoice", "subscription", "credit_notes"),
    ),
}


def clause_queries(dispute_type: str, text: str) -> list[str]:
    plan = PLAYBOOKS[dispute_type]
    return [text, *plan.clause_queries, *COMMON_QUERIES]


def evidence_period(
    dispute_type: str, start: date, end: date, explicit: bool, dispute_date: date
) -> tuple[date, date]:
    """The records window, which isn't always the month the customer named.

    In "I cancelled in March", March is when they cancelled, not the charge in dispute. So a
    cancellation always runs through the latest billed month, starting at the named month or, if
    none was named, at the lookback.
    """
    lookback = PLAYBOOKS[dispute_type].lookback_days
    if not lookback:
        return start, end
    latest = default_period(dispute_date)[1]
    window_end = max(end, latest)
    return (
        start if explicit else (window_end - timedelta(days=lookback)).replace(day=1)
    ), window_end


def gather_records(
    tools: Tools, dispute_type: str, account_ref: str, start: date, end: date
) -> list[EvidenceSet]:
    baseline_start, baseline_end = start - timedelta(days=90), start - timedelta(days=1)
    fetchers = {
        "invoice": lambda: tools.lookup_invoice(account_ref, start, end),
        "roaming_cdrs": lambda: tools.search_cdrs(account_ref, start, end, ["roaming"]),
        "international_cdrs": lambda: tools.search_cdrs(account_ref, start, end, _INTL),
        "oob_cdrs": lambda: tools.search_cdrs(account_ref, start, end, ["oob"]),
        "baseline_cdrs": lambda: tools.search_cdrs(
            account_ref, baseline_start, baseline_end, _INTL
        ),
        "subscription": lambda: tools.subscription_history(account_ref),
        "consents": lambda: tools.list_consents(account_ref),
        "notifications": lambda: tools.list_notifications(account_ref, start, end),
        "credit_notes": lambda: tools.list_credit_notes(account_ref),
        "payments": lambda: tools.list_payments(account_ref, start, end),
        "tariff_roaming": lambda: tools.get_tariff(account_ref, "roaming", start),
        "tariff_international": lambda: tools.get_tariff(account_ref, "international", start),
        "tariff_oob": lambda: tools.get_tariff(account_ref, "oob", start),
    }
    return [fetchers[name]() for name in PLAYBOOKS[dispute_type].records]
