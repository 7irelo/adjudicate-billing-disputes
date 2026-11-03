"""A thin synchronous MCP client implementing the same `Tools` interface as ToolService."""

import asyncio
import json
from datetime import date
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from adjudicate.schemas import Classification, Clause, EvidenceSet, FraudScreen
from adjudicate.tenancy.auth import issue_token


class McpToolError(Exception):
    pass


class McpTools:
    def __init__(self, url: str, tenant_id: str) -> None:
        self.url = url
        self.tenant_id = tenant_id

    async def _call_async(self, name: str, arguments: dict[str, Any]) -> Any:
        # A fresh short-lived token per call, so a long-paused graph never holds an expired one.
        headers = {"Authorization": f"Bearer {issue_token(self.tenant_id)}"}
        async with (
            httpx2.AsyncClient(headers=headers, timeout=120) as http,
            streamable_http_client(self.url, http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments)
        if result.is_error:
            raise McpToolError(f"{name} failed: {result.content}")
        structured = result.structured_content
        if structured and "result" in structured:
            return structured["result"]
        return result.content[0].text if result.content else None  # pyright: ignore[reportAttributeAccessIssue]

    def call(self, name: str, **arguments: Any) -> Any:
        return asyncio.run(self._call_async(name, arguments))

    def _evidence(self, name: str, **arguments: Any) -> EvidenceSet:
        return EvidenceSet.model_validate_json(self.call(name, **arguments))

    def account_exists(self, account_ref: str) -> bool:
        value = self.call("account_exists", account_ref=account_ref)
        return value is True or value == "true"

    def open_dispute(self, dispute_id: str, account_ref: str, text: str) -> None:
        self.call("open_dispute", dispute_id=dispute_id, account_ref=account_ref, text=text)

    def save_result(self, dispute_id: str, status: str, result: dict) -> None:
        self.call("save_result", dispute_id=dispute_id, status=status, result=result)

    def search_clauses(self, query: str, k: int = 8, as_of: date | None = None) -> list[dict]:
        on = as_of.isoformat() if as_of else None
        return json.loads(self.call("search_clauses", query=query, k=k, as_of=on))

    def get_clause(self, clause_id: str) -> Clause | None:
        data = json.loads(self.call("get_clause", clause_id=clause_id))
        return Clause.model_validate(data) if data else None

    def lookup_invoice(self, account_ref: str, start: date, end: date) -> EvidenceSet:
        return self._evidence(
            "lookup_invoice", account_ref=account_ref, start=str(start), end=str(end)
        )

    def search_cdrs(
        self, account_ref: str, start: date, end: date, classes: list[str] | None = None
    ) -> EvidenceSet:
        return self._evidence(
            "search_cdrs", account_ref=account_ref, start=str(start), end=str(end), classes=classes
        )

    def subscription_history(self, account_ref: str) -> EvidenceSet:
        return self._evidence("subscription_history", account_ref=account_ref)

    def list_consents(self, account_ref: str) -> EvidenceSet:
        return self._evidence("list_consents", account_ref=account_ref)

    def list_notifications(self, account_ref: str, start: date, end: date) -> EvidenceSet:
        return self._evidence(
            "list_notifications", account_ref=account_ref, start=str(start), end=str(end)
        )

    def list_credit_notes(self, account_ref: str) -> EvidenceSet:
        return self._evidence("list_credit_notes", account_ref=account_ref)

    def list_payments(self, account_ref: str, start: date, end: date) -> EvidenceSet:
        return self._evidence(
            "list_payments", account_ref=account_ref, start=str(start), end=str(end)
        )

    def get_tariff(self, account_ref: str, destination_class: str, as_of: date) -> EvidenceSet:
        return self._evidence(
            "get_tariff",
            account_ref=account_ref,
            destination_class=destination_class,
            as_of=str(as_of),
        )

    def screen_fraud(self, account_ref: str, start: date, end: date) -> FraudScreen:
        raw = self.call("screen_fraud", account_ref=account_ref, start=str(start), end=str(end))
        return FraudScreen.model_validate_json(raw)

    def classify_dispute(self, text: str) -> Classification:
        return Classification.model_validate_json(self.call("classify_dispute", text=text))

    def calculate_credit(
        self,
        rule_id: str,
        dispute_id: str,
        account_ref: str,
        start: date,
        end: date,
        dispute_date: date,
    ) -> dict[str, Any]:
        raw = self.call(
            "calculate_credit",
            rule_id=rule_id,
            dispute_id=dispute_id,
            account_ref=account_ref,
            start=str(start),
            end=str(end),
            dispute_date=str(dispute_date),
        )
        return json.loads(raw)

    def escalate_to_human(self, dispute_id: str, queue: str, reasons: list[str]) -> str:
        return self.call("escalate_to_human", dispute_id=dispute_id, queue=queue, reasons=reasons)

    def resolve_review(self, review_id: str, resolution: dict) -> None:
        self.call("resolve_review", review_id=review_id, resolution=resolution)
