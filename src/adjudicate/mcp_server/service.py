"""Tool implementations, shared by the MCP server and in-process callers.

`ToolService` is constructed for one tenant, so no method can take another tenant's name. Every
query goes through a tenant-scoped connection on the non-owner role, so RLS is the real boundary.
Nothing here can issue a credit or change billing data.
"""

import json
import math
from datetime import date, timedelta
from typing import Any, Protocol

from adjudicate.classify.infer import classify_text
from adjudicate.credit.calculator import AmbiguousFacts, NoEligibleLines, compute_credit
from adjudicate.credit.rules import load_policy
from adjudicate.db import TenantPool
from adjudicate.fraud.screen import screen
from adjudicate.records import queries
from adjudicate.retrieval import expansion, pipeline
from adjudicate.schemas import Classification, Clause, CreditComputation, EvidenceSet, FraudScreen


class Tools(Protocol):
    """What the graph needs. Implemented in-process (ToolService) and over MCP (McpTools)."""

    def account_exists(self, account_ref: str) -> bool: ...
    def open_dispute(self, dispute_id: str, account_ref: str, text: str) -> None: ...
    def save_result(self, dispute_id: str, status: str, result: dict) -> None: ...
    def search_clauses(self, query: str, k: int, as_of: date | None) -> list[dict]: ...
    def get_clause(self, clause_id: str) -> Clause | None: ...
    def lookup_invoice(self, account_ref: str, start: date, end: date) -> EvidenceSet: ...
    def search_cdrs(
        self, account_ref: str, start: date, end: date, classes: list[str] | None
    ) -> EvidenceSet: ...
    def subscription_history(self, account_ref: str) -> EvidenceSet: ...
    def list_consents(self, account_ref: str) -> EvidenceSet: ...
    def list_notifications(self, account_ref: str, start: date, end: date) -> EvidenceSet: ...
    def list_credit_notes(self, account_ref: str) -> EvidenceSet: ...
    def list_payments(self, account_ref: str, start: date, end: date) -> EvidenceSet: ...
    def get_tariff(self, account_ref: str, destination_class: str, as_of: date) -> EvidenceSet: ...
    def screen_fraud(self, account_ref: str, start: date, end: date) -> FraudScreen: ...
    def classify_dispute(self, text: str) -> Classification: ...
    def calculate_credit(
        self,
        rule_id: str,
        dispute_id: str,
        account_ref: str,
        start: date,
        end: date,
        dispute_date: date,
    ) -> dict[str, Any]: ...
    def escalate_to_human(self, dispute_id: str, queue: str, reasons: list[str]) -> str: ...
    def resolve_review(self, review_id: str, resolution: dict) -> None: ...


class ToolService:
    def __init__(self, pool: TenantPool, tenant_id: str) -> None:
        self.pool = pool
        self.tenant_id = tenant_id

    def _conn(self):
        return self.pool.tenant_conn(self.tenant_id)

    def account_exists(self, account_ref: str) -> bool:
        with self._conn() as conn:
            return queries.account_exists(conn, account_ref)

    def open_dispute(self, dispute_id: str, account_ref: str, text: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO disputes (tenant_id, dispute_id, account_ref, text) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, dispute_id) DO NOTHING",
                (self.tenant_id, dispute_id, account_ref, text),
            )

    def save_result(self, dispute_id: str, status: str, result: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE disputes SET status = %s, result = %s WHERE dispute_id = %s",
                (status, json.dumps(result, default=str), dispute_id),
            )

    def search_clauses(self, query: str, k: int = 8, as_of: date | None = None) -> list[dict]:
        with self._conn() as conn:
            hits = pipeline.search_clauses(conn, self.tenant_id, query, k=k, as_of=as_of)
            hits = expansion.with_definitions(conn, hits)
        return [h.model_dump(mode="json") for h in hits]

    def get_clause(self, clause_id: str) -> Clause | None:
        with self._conn() as conn:
            return pipeline.get_clause(conn, clause_id)

    def lookup_invoice(self, account_ref: str, start: date, end: date) -> EvidenceSet:
        with self._conn() as conn:
            return queries.lookup_invoice_lines(conn, account_ref, start, end)

    def search_cdrs(
        self, account_ref: str, start: date, end: date, classes: list[str] | None = None
    ) -> EvidenceSet:
        with self._conn() as conn:
            return queries.search_cdrs(conn, account_ref, start, end, classes)

    def subscription_history(self, account_ref: str) -> EvidenceSet:
        with self._conn() as conn:
            return queries.subscription_history(conn, account_ref)

    def list_consents(self, account_ref: str) -> EvidenceSet:
        with self._conn() as conn:
            return queries.consents(conn, account_ref)

    def list_notifications(self, account_ref: str, start: date, end: date) -> EvidenceSet:
        with self._conn() as conn:
            return queries.notifications(conn, account_ref, start, end)

    def list_credit_notes(self, account_ref: str) -> EvidenceSet:
        with self._conn() as conn:
            return queries.credit_notes(conn, account_ref)

    def list_payments(self, account_ref: str, start: date, end: date) -> EvidenceSet:
        with self._conn() as conn:
            return queries.payments(conn, account_ref, start, end)

    def get_tariff(self, account_ref: str, destination_class: str, as_of: date) -> EvidenceSet:
        with self._conn() as conn:
            return queries.get_tariff(conn, account_ref, destination_class, as_of)

    def screen_fraud(self, account_ref: str, start: date, end: date) -> FraudScreen:
        """Screen one period against the preceding 90 days. Takes no dispute text, by design."""
        customer_type = load_policy(self.tenant_id).customer_type
        baseline_end = start - timedelta(days=1)
        baseline_start = start - timedelta(days=90)
        with self._conn() as conn:
            rows = queries.cdr_rows(conn, account_ref, start, end)
            baseline = queries.cdr_rows(conn, account_ref, baseline_start, baseline_end)
        months = math.ceil((baseline_end - baseline_start).days / 30)
        return screen(rows, baseline, customer_type, baseline_months=months)

    def classify_dispute(self, text: str) -> Classification:
        return classify_text(text)

    def calculate_credit(
        self,
        rule_id: str,
        dispute_id: str,
        account_ref: str,
        start: date,
        end: date,
        dispute_date: date,
    ) -> dict[str, Any]:
        """Compute and store an idempotent draft. A retry or resume returns the same note.

        The rule version is the one in force at the start of the disputed period, so a May call is
        governed by the May terms even if they changed in June.
        """
        rule = load_policy(self.tenant_id).rule(rule_id, start)
        if rule is None:
            return {"status": "unknown_rule", "rule_id": rule_id}
        with self._conn() as conn:
            facts = queries.load_credit_facts(conn, account_ref, start, end, dispute_date)
            try:
                computation = compute_credit(rule, facts)
            except NoEligibleLines as exc:
                return {"status": "no_eligible_lines", "detail": str(exc)}
            except AmbiguousFacts as exc:
                return {"status": "ambiguous_facts", "detail": str(exc)}
            note_id = self._store_draft(conn, computation, dispute_id, account_ref)
        return {
            "status": "ok",
            "credit_note_id": note_id,
            "computation": computation.model_dump(mode="json"),
            "auto_credit_limit_cents": rule.auto_credit_limit_cents,
        }

    def _store_draft(
        self, conn, computation: CreditComputation, dispute_id: str, account_ref: str
    ) -> str:
        key = f"{dispute_id}:{computation.rule_id}:{computation.rule_version}"
        note_id = f"CN-{dispute_id}-{computation.rule_id}"[:60]
        inserted = conn.execute(
            "INSERT INTO credit_notes (tenant_id, credit_note_id, account_ref, dispute_id, "
            "idempotency_key, status, rule_id, rule_version, credit_cents, computation) "
            "VALUES (%s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING credit_note_id",
            (
                self.tenant_id,
                note_id,
                account_ref,
                dispute_id,
                key,
                computation.rule_id,
                computation.rule_version,
                computation.credit_cents,
                computation.model_dump_json(),
            ),
        ).fetchone()
        if inserted is None:
            existing = conn.execute(
                "SELECT credit_note_id FROM credit_notes WHERE idempotency_key = %s", (key,)
            ).fetchone()
            return existing["credit_note_id"]
        for line_id, amount in computation.allocations.items():
            conn.execute(
                "INSERT INTO credit_note_lines (tenant_id, credit_note_id, line_id, amount_cents) "
                "VALUES (%s, %s, %s, %s)",
                (self.tenant_id, note_id, line_id, amount),
            )
        return note_id

    def escalate_to_human(self, dispute_id: str, queue: str, reasons: list[str]) -> str:
        review_id = f"R-{dispute_id}-{queue}"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO review_items (tenant_id, review_id, dispute_id, queue, reasons) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, dispute_id, queue) DO NOTHING",
                (self.tenant_id, review_id, dispute_id, queue, json.dumps(reasons)),
            )
        return review_id

    def resolve_review(self, review_id: str, resolution: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE review_items SET status = 'resolved', resolution = %s WHERE review_id = %s",
                (json.dumps(resolution), review_id),
            )

    def list_reviews(self, status: str = "pending") -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT review_id, dispute_id, queue, reasons, status FROM review_items "
                "WHERE status = %s ORDER BY created_at",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_dispute(self, dispute_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT dispute_id, account_ref, status, result FROM disputes WHERE dispute_id = %s",
                (dispute_id,),
            ).fetchone()
        return dict(row) if row else None
