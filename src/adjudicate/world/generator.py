"""Deterministic world builder. Invoices are derived by rating CDRs, so they reconcile by design.

MSISDNs use the +27 00 prefix, which isn't allocated to mobile subscribers, so they can't belong to
a real person. Dates cover Dec 2025 to Jun 2026, and evals run with `AS_OF` as "today".
"""

import calendar
import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from adjudicate.credit.dates import SAST

AS_OF = date(2026, 6, 20)
FIRST_MONTH = (2025, 12)
LAST_MONTH = (2026, 5)  # the June invoice is issued on 1 July, after AS_OF

TABLES = (
    "tenants",
    "accounts",
    "tariff_plans",
    "tariff_rates",
    "bundles",
    "subscriptions",
    "subscription_events",
    "invoices",
    "invoice_lines",
    "cdrs",
    "notifications",
    "consents",
    "payments",
    "credit_notes",
    "credit_note_lines",
)

TENANTS = {
    "baobab": {"name": "Baobab Mobile", "customer_type": "consumer", "letter": "B"},
    "karoo": {"name": "Karoo Connect", "customer_type": "consumer", "letter": "K"},
    "fynbos": {"name": "Fynbos Voice", "customer_type": "business", "letter": "F"},
}

# Rates in cents per minute, VAT inclusive. `changes` are mid-world tariff changes.
PLANS = {
    "baobab": {
        "plan_id": "BAO-SMART",
        "name": "Baobab Smart 100",
        "fee": 29900,
        "minutes": 100,
        "rates": {
            "local": 120,
            "oob": 150,
            "international": 450,
            "premium": 1800,
            "roaming": 1500,
        },
        "changes": {"international": (date(2026, 4, 1), 520)},
    },
    "karoo": {
        "plan_id": "KAR-LITE",
        "name": "Karoo Lite 60",
        "fee": 19900,
        "minutes": 60,
        "rates": {
            "local": 130,
            "oob": 170,
            "international": 490,
            "premium": 1900,
            "roaming": 1650,
        },
        "changes": {},
    },
    "fynbos": {
        "plan_id": "FYN-TRUNK",
        "name": "Fynbos Hosted Trunk",
        "fee": 120000,
        "minutes": 1000,
        "rates": {"local": 60, "oob": 60, "international": 380, "premium": 1500, "roaming": 1500},
        "changes": {},
    },
}

INTERNATIONAL_NUMBERS = ["+44 20 7946", "+1 202 555", "+263 24 2700", "+91 22 5550"]
LINE_TYPE_BY_CLASS = {
    "international": "usage_international",
    "premium": "usage_international",
    "roaming": "usage_roaming",
}


def sast(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SAST).astimezone(UTC)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def months(first: tuple[int, int] = FIRST_MONTH, last: tuple[int, int] = LAST_MONTH):
    year, month = first
    while (year, month) <= last:
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


@dataclass
class ScenarioInfo:
    scenario_id: str
    tenant: str
    account_ref: str
    subscription_id: str
    records: dict[str, list[str]] = field(default_factory=dict)
    description: str = ""


class World:
    """Row collections per table, plus a manifest of what each scenario touched."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.rows: dict[str, list[dict]] = {t: [] for t in TABLES}
        self.scenarios: dict[tuple[str, str], ScenarioInfo] = {}
        self._counters: dict[tuple[str, str], int] = defaultdict(int)
        self._calls: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._invoices: dict[tuple[str, str], dict] = {}
        self._add_tenants()

    def next_id(self, tenant: str, prefix: str) -> str:
        self._counters[(tenant, prefix)] += 1
        letter = TENANTS[tenant]["letter"]
        return f"{prefix}-{letter}{self._counters[(tenant, prefix)]:06d}"

    def _add_tenants(self) -> None:
        for tenant, meta in TENANTS.items():
            self.rows["tenants"].append(
                {"tenant_id": tenant, "name": meta["name"], "customer_type": meta["customer_type"]}
            )
            plan = PLANS[tenant]
            self.rows["tariff_plans"].append(
                {
                    "tenant_id": tenant,
                    "plan_id": plan["plan_id"],
                    "name": plan["name"],
                    "monthly_fee_cents": plan["fee"],
                }
            )
            self.rows["bundles"].append(
                {
                    "tenant_id": tenant,
                    "bundle_id": f"{plan['plan_id']}-BUNDLE",
                    "plan_id": plan["plan_id"],
                    "included_minutes": plan["minutes"],
                }
            )
            for dest, cents in plan["rates"].items():
                change = plan["changes"].get(dest)
                self.rows["tariff_rates"].append(
                    {
                        "tenant_id": tenant,
                        "rate_id": self.next_id(tenant, "TR"),
                        "plan_id": plan["plan_id"],
                        "destination_class": dest,
                        "cents_per_minute": cents,
                        "effective_from": date(2025, 1, 1),
                        "effective_to": change[0] - timedelta(days=1) if change else None,
                        "clause_id": None,
                    }
                )
                if change:
                    self.rows["tariff_rates"].append(
                        {
                            "tenant_id": tenant,
                            "rate_id": self.next_id(tenant, "TR"),
                            "plan_id": plan["plan_id"],
                            "destination_class": dest,
                            "cents_per_minute": change[1],
                            "effective_from": change[0],
                            "effective_to": None,
                            "clause_id": None,
                        }
                    )

    def rate_on(self, tenant: str, dest: str, on: date) -> int:
        plan = PLANS[tenant]
        change = plan["changes"].get(dest)
        if change and on >= change[0]:
            return change[1]
        return plan["rates"][dest]

    def add_account(
        self,
        tenant: str,
        scenario_id: str | None = None,
        holder: str | None = None,
        opened: date = date(2025, 6, 1),
    ) -> tuple[str, str]:
        account_ref = self.next_id(tenant, "A")
        subscription_id = self.next_id(tenant, "S")
        number = self._counters[(tenant, "A")]
        letter_code = {"baobab": 1, "karoo": 2, "fynbos": 3}[tenant]
        self.rows["accounts"].append(
            {
                "tenant_id": tenant,
                "account_ref": account_ref,
                "holder_name": holder or f"Test Holder {letter_code}{number:05d}",
                "customer_type": TENANTS[tenant]["customer_type"],
                "msisdn": f"+2700{letter_code}{number:06d}",
                "opened_on": opened,
                "scenario_id": scenario_id,
            }
        )
        self.rows["subscriptions"].append(
            {
                "tenant_id": tenant,
                "subscription_id": subscription_id,
                "account_ref": account_ref,
                "plan_id": PLANS[tenant]["plan_id"],
                "started_on": opened,
                "fixed_term_months": 24,
            }
        )
        self.add_event(tenant, subscription_id, "activation", opened)
        return account_ref, subscription_id

    def add_event(
        self, tenant: str, subscription_id: str, kind: str, on: date, detail: dict | None = None
    ) -> str:
        event_id = self.next_id(tenant, "SE")
        self.rows["subscription_events"].append(
            {
                "tenant_id": tenant,
                "event_id": event_id,
                "subscription_id": subscription_id,
                "event_type": kind,
                "occurred_on": on,
                "detail": json.dumps(detail or {}),
            }
        )
        return event_id

    def add_consent(
        self,
        tenant: str,
        subscription_id: str,
        kind: str,
        granted: date,
        revoked: date | None = None,
    ) -> str:
        consent_id = self.next_id(tenant, "CO")
        self.rows["consents"].append(
            {
                "tenant_id": tenant,
                "consent_id": consent_id,
                "subscription_id": subscription_id,
                "kind": kind,
                "granted_on": granted,
                "revoked_on": revoked,
            }
        )
        return consent_id

    def add_notification(self, tenant: str, subscription_id: str, kind: str, at: datetime) -> str:
        notification_id = self.next_id(tenant, "NT")
        self.rows["notifications"].append(
            {
                "tenant_id": tenant,
                "notification_id": notification_id,
                "subscription_id": subscription_id,
                "kind": kind,
                "sent_at": at,
            }
        )
        return notification_id

    def add_call(
        self,
        tenant: str,
        subscription_id: str,
        at: datetime,
        seconds: int,
        destination: str,
        dest_class: str,
        direction: str = "outbound",
        rate_override: int | None = None,
    ) -> str:
        """Add a CDR rated per started minute at the tariff in force on the SA date of the call."""
        cdr_id = self.next_id(tenant, "CDR")
        billed = 0
        if direction == "outbound" and dest_class != "local":
            rate = rate_override
            if rate is None:
                rate = self.rate_on(tenant, dest_class, at.astimezone(SAST).date())
            billed = math.ceil(seconds / 60) * rate
        row = {
            "tenant_id": tenant,
            "cdr_id": cdr_id,
            "subscription_id": subscription_id,
            "started_at": at,
            "duration_seconds": seconds,
            "destination": destination,
            "destination_class": dest_class,
            "direction": direction,
            "billed_cents": billed,
            "invoice_line_id": None,
        }
        self.rows["cdrs"].append(row)
        self._calls[(tenant, subscription_id)].append(row)
        return cdr_id

    def add_invoice(self, tenant: str, account_ref: str, year: int, month: int) -> str:
        start, end = month_bounds(year, month)
        invoice_id = self.next_id(tenant, "INV")
        issue_year, issue_month = next_month(year, month)
        row = {
            "tenant_id": tenant,
            "invoice_id": invoice_id,
            "account_ref": account_ref,
            "period_start": start,
            "period_end": end,
            "issued_on": date(issue_year, issue_month, 1),
            "total_cents": 0,
        }
        self.rows["invoices"].append(row)
        self._invoices[(tenant, invoice_id)] = row
        return invoice_id

    def add_line(
        self,
        tenant: str,
        invoice_id: str,
        subscription_id: str,
        line_type: str,
        description: str,
        start: date,
        end: date,
        amount: int,
        quantity: int = 1,
    ) -> str:
        line_id = self.next_id(tenant, "IL")
        self.rows["invoice_lines"].append(
            {
                "tenant_id": tenant,
                "line_id": line_id,
                "invoice_id": invoice_id,
                "subscription_id": subscription_id,
                "line_type": line_type,
                "description": description,
                "service_start": start,
                "service_end": end,
                "quantity": quantity,
                "amount_cents": amount,
            }
        )
        self._invoices[(tenant, invoice_id)]["total_cents"] += amount
        return line_id

    def bill_month(
        self,
        tenant: str,
        account_ref: str,
        subscription_id: str,
        year: int,
        month: int,
        recurring: bool = True,
    ) -> dict[str, str]:
        """Derive one month's invoice from the CDRs. Returns {line_type: line_id}."""
        start, end = month_bounds(year, month)
        invoice_id = self.add_invoice(tenant, account_ref, year, month)
        lines: dict[str, str] = {"invoice": invoice_id}
        plan = PLANS[tenant]
        if recurring:
            lines["recurring"] = self.add_line(
                tenant,
                invoice_id,
                subscription_id,
                "recurring",
                f"{plan['name']} monthly fee",
                start,
                end,
                plan["fee"],
            )

        calls = [
            c
            for c in self._calls[(tenant, subscription_id)]
            if c["invoice_line_id"] is None
            and start <= c["started_at"].astimezone(SAST).date() <= end
        ]
        grouped: dict[str, list[dict]] = defaultdict(list)
        for call in calls:
            if call["direction"] == "outbound" and call["billed_cents"] > 0:
                grouped[LINE_TYPE_BY_CLASS.get(call["destination_class"], "usage_oob")].append(call)
        labels = {
            "usage_international": "International calls",
            "usage_roaming": "Roaming usage",
            "usage_oob": "Out-of-bundle usage",
        }
        for line_type, group in sorted(grouped.items()):
            line_id = self.add_line(
                tenant,
                invoice_id,
                subscription_id,
                line_type,
                labels[line_type],
                start,
                end,
                sum(c["billed_cents"] for c in group),
                quantity=len(group),
            )
            lines[line_type] = line_id
            for call in group:
                call["invoice_line_id"] = line_id
        return lines

    def add_payment(self, tenant: str, invoice_id: str, on: date, amount: int) -> str:
        payment_id = self.next_id(tenant, "PY")
        self.rows["payments"].append(
            {
                "tenant_id": tenant,
                "payment_id": payment_id,
                "invoice_id": invoice_id,
                "paid_on": on,
                "amount_cents": amount,
            }
        )
        return payment_id

    def add_issued_credit(
        self, tenant: str, account_ref: str, line_id: str, amount: int, on: date
    ) -> str:
        note_id = self.next_id(tenant, "CN")
        self.rows["credit_notes"].append(
            {
                "tenant_id": tenant,
                "credit_note_id": note_id,
                "account_ref": account_ref,
                "dispute_id": None,
                "idempotency_key": f"seed:{note_id}",
                "status": "issued",
                "rule_id": None,
                "rule_version": None,
                "credit_cents": amount,
                "computation": json.dumps({"seeded_on": on.isoformat()}),
            }
        )
        self.rows["credit_note_lines"].append(
            {
                "tenant_id": tenant,
                "credit_note_id": note_id,
                "line_id": line_id,
                "amount_cents": amount,
            }
        )
        return note_id

    def background_account(self, tenant: str) -> None:
        """An ordinary customer: local calls within the bundle, a few international calls."""
        account_ref, sub = self.add_account(tenant)
        self.add_consent(tenant, sub, "international_enabled", date(2025, 6, 1))
        rng = self.rng
        for year, month in months():
            _, end = month_bounds(year, month)
            for _ in range(rng.randint(8, 30)):
                self.add_call(
                    tenant,
                    sub,
                    sast(
                        year, month, rng.randint(1, end.day), rng.randint(7, 20), rng.randint(0, 59)
                    ),
                    rng.randint(20, 240),
                    f"+27 21 555 {rng.randint(0, 9999):04d}",
                    "local",
                )
            for _ in range(rng.randint(0, 2)):
                self.add_call(
                    tenant,
                    sub,
                    sast(
                        year, month, rng.randint(1, end.day), rng.randint(8, 20), rng.randint(0, 59)
                    ),
                    rng.randint(60, 600),
                    f"{rng.choice(INTERNATIONAL_NUMBERS)} {rng.randint(0, 9999):04d}",
                    "international",
                )
            lines = self.bill_month(tenant, account_ref, sub, year, month)
            invoice = lines["invoice"]
            issue_year, issue_month = next_month(year, month)
            total = self._invoices[(tenant, invoice)]["total_cents"]
            self.add_payment(tenant, invoice, date(issue_year, issue_month, 7), total)

    def snapshot_hash(self) -> str:
        """A hash of every row and scenario, stamped on eval runs."""
        payload = json.dumps(
            {
                "rows": self.rows,
                "scenarios": {f"{k[0]}:{k[1]}": v.records for k, v in self.scenarios.items()},
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
