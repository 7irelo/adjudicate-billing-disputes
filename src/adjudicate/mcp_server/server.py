"""MCP server over Streamable HTTP. A bearer token binds the tenant for the whole request.

Tool schemas have no tenant parameter, so a caller can't even express a request for another tenant.
"""

import argparse
import json
from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from adjudicate.db import TenantPool
from adjudicate.mcp_server.service import ToolService
from adjudicate.tenancy.auth import AuthError, verify_token
from adjudicate.tenancy.session import bind_tenant, current_tenant, reset_tenant

mcp = MCPServer("adjudicate")
_pool: TenantPool | None = None


def service() -> ToolService:
    global _pool
    if _pool is None:
        _pool = TenantPool()
    return ToolService(_pool, current_tenant())


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


@mcp.tool()
def account_exists(account_ref: str) -> bool:
    """Whether this account exists for the caller. Another tenant's account returns false."""
    return service().account_exists(account_ref)


@mcp.tool()
def open_dispute(dispute_id: str, account_ref: str, text: str) -> str:
    """Record a new dispute."""
    service().open_dispute(dispute_id, account_ref, text)
    return "ok"


@mcp.tool()
def save_result(dispute_id: str, status: str, result: dict) -> str:
    """Store a dispute's outcome."""
    service().save_result(dispute_id, status, result)
    return "ok"


@mcp.tool()
def search_clauses(query: str, k: int = 8, as_of: str | None = None) -> str:
    """Search the operator's terms and shared regulations, in force on `as_of`."""
    on = date.fromisoformat(as_of) if as_of else None
    return json.dumps(service().search_clauses(query, k, on))


@mcp.tool()
def get_clause(clause_id: str) -> str:
    """Fetch one clause by ID. Unknown and foreign IDs both return null."""
    return json.dumps(_dump(service().get_clause(clause_id)))


@mcp.tool()
def lookup_invoice(account_ref: str, start: str, end: str) -> str:
    """Invoice lines for a period, as a summary and an evidence handle."""
    return (
        service()
        .lookup_invoice(account_ref, date.fromisoformat(start), date.fromisoformat(end))
        .model_dump_json()
    )


@mcp.tool()
def search_cdrs(account_ref: str, start: str, end: str, classes: list[str] | None = None) -> str:
    """Call records as aggregates and a handle, never raw rows."""
    return (
        service()
        .search_cdrs(account_ref, date.fromisoformat(start), date.fromisoformat(end), classes)
        .model_dump_json()
    )


@mcp.tool()
def subscription_history(account_ref: str) -> str:
    """Subscription events and any computed cancellation effective date."""
    return service().subscription_history(account_ref).model_dump_json()


@mcp.tool()
def list_consents(account_ref: str) -> str:
    """Opt-ins and activations on the account."""
    return service().list_consents(account_ref).model_dump_json()


@mcp.tool()
def list_notifications(account_ref: str, start: str, end: str) -> str:
    """Depletion and bill-shock messages sent in a period."""
    return (
        service()
        .list_notifications(account_ref, date.fromisoformat(start), date.fromisoformat(end))
        .model_dump_json()
    )


@mcp.tool()
def list_credit_notes(account_ref: str) -> str:
    """Credit notes already issued on the account."""
    return service().list_credit_notes(account_ref).model_dump_json()


@mcp.tool()
def list_payments(account_ref: str, start: str, end: str) -> str:
    """Payments against invoices in a period."""
    return (
        service()
        .list_payments(account_ref, date.fromisoformat(start), date.fromisoformat(end))
        .model_dump_json()
    )


@mcp.tool()
def get_tariff(account_ref: str, destination_class: str, as_of: str) -> str:
    """The rate for a destination class in force on a date."""
    return (
        service()
        .get_tariff(account_ref, destination_class, date.fromisoformat(as_of))
        .model_dump_json()
    )


@mcp.tool()
def screen_fraud(account_ref: str, start: str, end: str) -> str:
    """Deterministic fraud signals from call records. Takes no dispute text."""
    return (
        service()
        .screen_fraud(account_ref, date.fromisoformat(start), date.fromisoformat(end))
        .model_dump_json()
    )


@mcp.tool()
def classify_dispute(text: str) -> str:
    """Dispute type and urgency, with probabilities."""
    return service().classify_dispute(text).model_dump_json()


@mcp.tool()
def calculate_credit(
    rule_id: str, dispute_id: str, account_ref: str, start: str, end: str, dispute_date: str
) -> str:
    """Compute a credit in code and store an idempotent DRAFT. It can't issue a credit."""
    return json.dumps(
        service().calculate_credit(
            rule_id,
            dispute_id,
            account_ref,
            date.fromisoformat(start),
            date.fromisoformat(end),
            date.fromisoformat(dispute_date),
        )
    )


@mcp.tool()
def escalate_to_human(dispute_id: str, queue: str, reasons: list[str]) -> str:
    """Put a dispute on the human or fraud review queue. Returns the review ID."""
    return service().escalate_to_human(dispute_id, queue, reasons)


@mcp.tool()
def resolve_review(review_id: str, resolution: dict) -> str:
    """Record a reviewer's resolution."""
    service().resolve_review(review_id, resolution)
    return "ok"


class TenantTokenMiddleware:
    """Reject anything without a valid token, and bind its tenant for this request only.

    Pure ASGI, so the tenant context var is set in the same context the tools inherit.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = Headers(scope=scope).get("authorization", "")
        if not header.lower().startswith("bearer "):
            response = JSONResponse({"error": "missing bearer token"}, status_code=401)
            await response(scope, receive, send)
            return
        try:
            tenant = verify_token(header[7:])
        except AuthError:
            await JSONResponse({"error": "invalid token"}, status_code=401)(scope, receive, send)
            return
        token = bind_tenant(tenant)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_tenant(token)


def build_app(host: str = "127.0.0.1") -> Starlette:
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=host)
    app.add_middleware(TenantTokenMiddleware)
    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(build_app(args.host), host=args.host, port=args.port)
