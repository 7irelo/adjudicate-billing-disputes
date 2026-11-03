"""Record queries. All run on a tenant-scoped connection, so RLS is the boundary, not these WHEREs.

An account that doesn't exist and another tenant's account give the same empty result, so the tools
can't be used as an existence oracle.
"""

import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from adjudicate.credit.calculator import Consent, CreditFacts, Line, Notification
from adjudicate.credit.dates import SAST, local_date
from adjudicate.db import DictConnection
from adjudicate.schemas import EvidenceSet, RecordType


def make_evidence(
    record_type: RecordType, query: dict[str, Any], summary: dict[str, Any], record_ids: list[str]
) -> EvidenceSet:
    digest = hashlib.sha256(
        json.dumps([record_type, query, record_ids], sort_keys=True, default=str).encode()
    ).hexdigest()
    return EvidenceSet(
        handle=f"ev_{digest[:8]}",
        record_type=record_type,
        query=query,
        summary=summary,
        record_ids=record_ids,
    )


def _subscriptions(conn: DictConnection, account_ref: str) -> list[str]:
    rows = conn.execute(
        "SELECT subscription_id FROM subscriptions WHERE account_ref = %s", (account_ref,)
    ).fetchall()
    return [r["subscription_id"] for r in rows]


def account_exists(conn: DictConnection, account_ref: str) -> bool:
    return bool(_subscriptions(conn, account_ref))


def lookup_invoice_lines(
    conn: DictConnection, account_ref: str, period_start: date, period_end: date
) -> EvidenceSet:
    rows = conn.execute(
        """
        SELECT l.line_id, l.line_type, l.description, l.service_start, l.service_end, l.quantity,
               l.amount_cents, i.invoice_id, i.issued_on,
               COALESCE((SELECT sum(c.amount_cents) FROM credit_note_lines c
                         JOIN credit_notes n USING (tenant_id, credit_note_id)
                         WHERE c.line_id = l.line_id AND n.status = 'issued'), 0)::bigint AS prior
        FROM invoice_lines l JOIN invoices i USING (tenant_id, invoice_id)
        WHERE i.account_ref = %s AND l.service_start <= %s AND l.service_end >= %s
        ORDER BY l.line_id
        """,
        (account_ref, period_end, period_start),
    ).fetchall()
    summary = {
        "count": len(rows),
        "total_cents": sum(r["amount_cents"] for r in rows),
        "lines": [
            {
                "line_id": r["line_id"],
                "type": r["line_type"],
                "description": r["description"],
                "service_start": r["service_start"].isoformat(),
                "service_end": r["service_end"].isoformat(),
                "invoice_id": r["invoice_id"],
                "issued_on": r["issued_on"].isoformat(),
                "quantity": r["quantity"],
                "amount_cents": r["amount_cents"],
                "prior_credits_cents": r["prior"],
            }
            for r in rows
        ],
    }
    query = {"account_ref": account_ref, "from": str(period_start), "to": str(period_end)}
    return make_evidence("invoice_line", query, summary, [r["line_id"] for r in rows])


def _rate_on(conn: DictConnection, plan_id: str, dest: str, on: date) -> int | None:
    row = conn.execute(
        "SELECT cents_per_minute FROM tariff_rates WHERE plan_id = %s AND destination_class = %s "
        "AND effective_from <= %s AND (effective_to IS NULL OR effective_to >= %s)",
        (plan_id, dest, on, on),
    ).fetchone()
    return row["cents_per_minute"] if row else None


def cdr_rows(
    conn: DictConnection,
    account_ref: str,
    start: date,
    end: date,
    classes: list[str] | None = None,
) -> list[dict]:
    start_at = datetime(start.year, start.month, start.day, tzinfo=SAST)
    end_at = datetime(end.year, end.month, end.day, tzinfo=SAST) + timedelta(days=1)
    return conn.execute(
        """
        SELECT c.*, s.plan_id FROM cdrs c JOIN subscriptions s USING (tenant_id, subscription_id)
        WHERE s.account_ref = %s AND c.started_at >= %s AND c.started_at < %s
          AND (%s::text[] IS NULL OR c.destination_class = ANY(%s::text[]))
        ORDER BY c.started_at, c.cdr_id
        """,
        (account_ref, start_at, end_at, classes, classes),
    ).fetchall()


def rerated_cents(conn: DictConnection, rows: list[dict]) -> int:
    """Re-rate calls at the tariff in force on each call's SA date. The model never does this."""
    total = 0
    for r in rows:
        if r["direction"] != "outbound" or r["billed_cents"] == 0:
            continue
        rate = _rate_on(conn, r["plan_id"], r["destination_class"], local_date(r["started_at"]))
        total += r["billed_cents"] if rate is None else math.ceil(r["duration_seconds"] / 60) * rate
    return total


def search_cdrs(
    conn: DictConnection,
    account_ref: str,
    start: date,
    end: date,
    classes: list[str] | None = None,
) -> EvidenceSet:
    rows = cdr_rows(conn, account_ref, start, end, classes)
    prefixes: Counter[str] = Counter(" ".join(r["destination"].split()[:1]) for r in rows)
    outbound = [r for r in rows if r["direction"] == "outbound"]
    billed = sum(r["billed_cents"] for r in rows)
    correct = rerated_cents(conn, rows)
    summary = {
        "count": len(rows),
        "outbound_count": len(outbound),
        "minutes": sum(math.ceil(r["duration_seconds"] / 60) for r in outbound),
        "billed_cents": billed,
        "correct_at_tariff_cents": correct,
        "overbilled_cents": max(0, billed - correct),
        "destinations": dict(prefixes.most_common(8)),
        "first": rows[0]["started_at"].astimezone(SAST).isoformat() if rows else None,
        "last": rows[-1]["started_at"].astimezone(SAST).isoformat() if rows else None,
    }
    query = {
        "account_ref": account_ref,
        "from": str(start),
        "to": str(end),
        "classes": classes,
    }
    return make_evidence("cdr", query, summary, [r["cdr_id"] for r in rows])


def subscription_history(conn: DictConnection, account_ref: str) -> EvidenceSet:
    rows = conn.execute(
        """
        SELECT e.event_id, e.event_type, e.occurred_on, s.plan_id, s.started_on
        FROM subscription_events e JOIN subscriptions s USING (tenant_id, subscription_id)
        WHERE s.account_ref = %s ORDER BY e.occurred_on, e.event_id
        """,
        (account_ref,),
    ).fetchall()
    summary = {
        "count": len(rows),
        "events": [
            {"event_id": r["event_id"], "type": r["event_type"], "on": r["occurred_on"].isoformat()}
            for r in rows
        ],
    }
    effective = [r for r in rows if r["event_type"] == "cancellation_effective"]
    if effective:
        summary["cancellation_effective_date"] = effective[-1]["occurred_on"].isoformat()
    return make_evidence(
        "subscription_event",
        {"account_ref": account_ref},
        summary,
        [r["event_id"] for r in rows],
    )


def consents(conn: DictConnection, account_ref: str) -> EvidenceSet:
    rows = conn.execute(
        """
        SELECT c.consent_id, c.kind, c.granted_on, c.revoked_on
        FROM consents c JOIN subscriptions s USING (tenant_id, subscription_id)
        WHERE s.account_ref = %s ORDER BY c.granted_on, c.consent_id
        """,
        (account_ref,),
    ).fetchall()
    summary = {
        "count": len(rows),
        "consents": [
            {
                "consent_id": r["consent_id"],
                "kind": r["kind"],
                "granted_on": r["granted_on"].isoformat(),
                "revoked_on": r["revoked_on"].isoformat() if r["revoked_on"] else None,
            }
            for r in rows
        ],
    }
    return make_evidence(
        "consent", {"account_ref": account_ref}, summary, [r["consent_id"] for r in rows]
    )


def notifications(conn: DictConnection, account_ref: str, start: date, end: date) -> EvidenceSet:
    start_at = datetime(start.year, start.month, start.day, tzinfo=SAST)
    end_at = datetime(end.year, end.month, end.day, tzinfo=SAST) + timedelta(days=1)
    rows = conn.execute(
        """
        SELECT n.notification_id, n.kind, n.sent_at
        FROM notifications n JOIN subscriptions s USING (tenant_id, subscription_id)
        WHERE s.account_ref = %s AND n.sent_at >= %s AND n.sent_at < %s
        ORDER BY n.sent_at, n.notification_id
        """,
        (account_ref, start_at, end_at),
    ).fetchall()
    summary = {
        "count": len(rows),
        "notifications": [
            {
                "notification_id": r["notification_id"],
                "kind": r["kind"],
                "sent_on": local_date(r["sent_at"]).isoformat(),
            }
            for r in rows
        ],
    }
    query = {"account_ref": account_ref, "from": str(start), "to": str(end)}
    return make_evidence("notification", query, summary, [r["notification_id"] for r in rows])


def credit_notes(conn: DictConnection, account_ref: str) -> EvidenceSet:
    rows = conn.execute(
        "SELECT credit_note_id, credit_cents FROM credit_notes "
        "WHERE account_ref = %s AND status = 'issued' ORDER BY credit_note_id",
        (account_ref,),
    ).fetchall()
    summary = {"count": len(rows), "total_cents": sum(r["credit_cents"] for r in rows)}
    return make_evidence(
        "credit_note", {"account_ref": account_ref}, summary, [r["credit_note_id"] for r in rows]
    )


def payments(conn: DictConnection, account_ref: str, start: date, end: date) -> EvidenceSet:
    rows = conn.execute(
        """
        SELECT p.payment_id, p.amount_cents, p.paid_on FROM payments p
        JOIN invoices i USING (tenant_id, invoice_id)
        WHERE i.account_ref = %s AND i.period_start <= %s AND i.period_end >= %s
        ORDER BY p.payment_id
        """,
        (account_ref, end, start),
    ).fetchall()
    summary = {"count": len(rows), "total_cents": sum(r["amount_cents"] for r in rows)}
    query = {"account_ref": account_ref, "from": str(start), "to": str(end)}
    return make_evidence("payment", query, summary, [r["payment_id"] for r in rows])


def get_tariff(
    conn: DictConnection, account_ref: str, destination_class: str, as_of: date
) -> EvidenceSet:
    rows = conn.execute(
        """
        SELECT r.rate_id, r.plan_id, r.destination_class, r.cents_per_minute, r.effective_from,
               r.effective_to
        FROM tariff_rates r JOIN subscriptions s USING (tenant_id, plan_id)
        WHERE s.account_ref = %s AND r.destination_class = %s AND r.effective_from <= %s
          AND (r.effective_to IS NULL OR r.effective_to >= %s)
        """,
        (account_ref, destination_class, as_of, as_of),
    ).fetchall()
    summary = {
        "count": len(rows),
        "rates": [
            {
                "rate_id": r["rate_id"],
                "plan_id": r["plan_id"],
                "destination_class": r["destination_class"],
                "cents_per_minute": r["cents_per_minute"],
                "in_force_on": str(as_of),
            }
            for r in rows
        ],
    }
    query = {
        "account_ref": account_ref,
        "destination_class": destination_class,
        "as_of": str(as_of),
    }
    return make_evidence("tariff_rate", query, summary, [r["rate_id"] for r in rows])


def load_credit_facts(
    conn: DictConnection, account_ref: str, period_start: date, period_end: date, dispute_date: date
) -> CreditFacts:
    """Assemble the calculator's inputs from records. Nothing here comes from dispute text."""
    lines: list[Line] = []
    rows = conn.execute(
        """
        SELECT l.*, i.issued_on,
               COALESCE((SELECT sum(c.amount_cents) FROM credit_note_lines c
                         JOIN credit_notes n USING (tenant_id, credit_note_id)
                         WHERE c.line_id = l.line_id AND n.status = 'issued'), 0)::bigint AS prior
        FROM invoice_lines l JOIN invoices i USING (tenant_id, invoice_id)
        WHERE i.account_ref = %s AND l.service_start <= %s AND l.service_end >= %s
        """,
        (account_ref, period_end, period_start),
    ).fetchall()
    for r in rows:
        correct = None
        if r["line_type"].startswith("usage_"):
            calls = conn.execute(
                "SELECT c.*, s.plan_id FROM cdrs c JOIN subscriptions s "
                "USING (tenant_id, subscription_id) WHERE c.invoice_line_id = %s",
                (r["line_id"],),
            ).fetchall()
            correct = rerated_cents(conn, calls) if calls else None
        lines.append(
            Line(
                line_id=r["line_id"],
                line_type=r["line_type"],
                service_start=r["service_start"],
                service_end=r["service_end"],
                amount_cents=r["amount_cents"],
                issued_on=r["issued_on"],
                subscription_id=r["subscription_id"],
                description=r["description"],
                prior_credit_cents=r["prior"],
                correct_cents=correct,
            )
        )
    effective = conn.execute(
        """
        SELECT max(e.occurred_on) AS on_date FROM subscription_events e
        JOIN subscriptions s USING (tenant_id, subscription_id)
        WHERE s.account_ref = %s AND e.event_type = 'cancellation_effective'
        """,
        (account_ref,),
    ).fetchone()
    consent_rows = conn.execute(
        "SELECT c.kind, c.granted_on, c.revoked_on FROM consents c "
        "JOIN subscriptions s USING (tenant_id, subscription_id) WHERE s.account_ref = %s",
        (account_ref,),
    ).fetchall()
    notification_rows = conn.execute(
        "SELECT n.kind, n.sent_at FROM notifications n "
        "JOIN subscriptions s USING (tenant_id, subscription_id) WHERE s.account_ref = %s",
        (account_ref,),
    ).fetchall()
    return CreditFacts(
        lines=tuple(lines),
        dispute_date=dispute_date,
        cancellation_effective=effective["on_date"] if effective else None,
        consents=tuple(Consent(c["kind"], c["granted_on"], c["revoked_on"]) for c in consent_rows),
        notifications=tuple(
            Notification(n["kind"], local_date(n["sent_at"])) for n in notification_rows
        ),
    )
