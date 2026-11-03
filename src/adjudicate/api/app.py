"""FastAPI service with durable review: a paused graph survives a restart through Postgres."""

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from adjudicate.config import get_settings
from adjudicate.db import TenantPool
from adjudicate.graph.build import build_graph, thread_config
from adjudicate.llm import LLM, default_llm
from adjudicate.mcp_client import McpTools
from adjudicate.mcp_server.service import Tools, ToolService
from adjudicate.tenancy.auth import AuthError, tenant_from_api_key
from adjudicate.world.generator import AS_OF


class DisputeRequest(BaseModel):
    account_ref: str
    text: str


class ReviewRequest(BaseModel):
    action: str
    note: str = ""


@dataclass
class Services:
    pool: TenantPool
    checkpoint_pool: ConnectionPool[Any]
    checkpointer: PostgresSaver
    llm: LLM


services: Services | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global services
    settings = get_settings()
    checkpoint_pool: ConnectionPool[Any] = ConnectionPool(
        settings.checkpoint_dsn,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    checkpointer = PostgresSaver(checkpoint_pool)  # pyright: ignore[reportArgumentType]
    checkpointer.setup()
    services = Services(TenantPool(), checkpoint_pool, checkpointer, default_llm())
    yield
    services.pool.close()
    checkpoint_pool.close()
    services = None


app = FastAPI(title="Adjudicate", lifespan=lifespan)


def require_services() -> Services:
    if services is None:
        raise HTTPException(503, "not ready")
    return services


def tenant_id(x_api_key: str = Header(default="")) -> str:
    try:
        return tenant_from_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc


def make_tools(svc: Services, tenant: str) -> Tools:
    """Over MCP by default. Direct calls exist for tests, and to measure the MCP hop's cost."""
    if os.environ.get("ADJUDICATE_DIRECT_TOOLS") == "1":
        return ToolService(svc.pool, tenant)
    return McpTools(get_settings().mcp_url, tenant)


def _pending(snapshot) -> dict[str, Any]:
    pending = snapshot.tasks[0].interrupts[0].value
    return {"status": "pending_review", **pending}


@app.post("/disputes")
def create_dispute(
    body: DisputeRequest,
    tenant: str = Depends(tenant_id),
    svc: Services = Depends(require_services),
) -> dict[str, Any]:
    tools = make_tools(svc, tenant)
    if not tools.account_exists(body.account_ref):
        raise HTTPException(404, "account not found")
    dispute_id = f"D-{uuid.uuid4().hex[:10]}"
    graph = build_graph(tools, svc.llm, checkpointer=svc.checkpointer)
    config = thread_config(tenant, dispute_id)
    graph.invoke(
        {
            "dispute_id": dispute_id,
            "tenant_id": tenant,
            "account_ref": body.account_ref,
            "text": body.text,
            # The synthetic world is frozen at AS_OF, so "today" must be too.
            "dispute_date": AS_OF.isoformat(),
            "options": {},
            "usage": [],
            "node_ms": [],
        },
        config,
    )
    snapshot = graph.get_state(config)
    if snapshot.next:
        return {
            "dispute_id": dispute_id,
            **_pending(snapshot),
            "state": _public_state(snapshot.values),
        }
    return {"dispute_id": dispute_id, "status": "closed", "result": snapshot.values["result"]}


def _public_state(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": values.get("decision"),
        "credit": values.get("credit"),
        "classification": values.get("classification"),
        "fraud": values.get("fraud"),
    }


@app.get("/disputes/{dispute_id}")
def get_dispute(
    dispute_id: str, tenant: str = Depends(tenant_id), svc: Services = Depends(require_services)
) -> dict[str, Any]:
    found = ToolService(svc.pool, tenant).get_dispute(dispute_id)
    if not found:  # the same answer for a foreign ID and a missing ID
        raise HTTPException(404, "dispute not found")
    return found


@app.get("/reviews")
def list_reviews(
    tenant: str = Depends(tenant_id), svc: Services = Depends(require_services)
) -> list[dict]:
    return ToolService(svc.pool, tenant).list_reviews()


@app.post("/reviews/{review_id}")
def submit_review(
    review_id: str,
    body: ReviewRequest,
    tenant: str = Depends(tenant_id),
    svc: Services = Depends(require_services),
) -> dict[str, Any]:
    store = ToolService(svc.pool, tenant)
    pending = {r["review_id"]: r for r in store.list_reviews()}
    if review_id not in pending:  # also covers double submission and foreign IDs
        raise HTTPException(404, "review not found")
    dispute_id = pending[review_id]["dispute_id"]
    graph = build_graph(make_tools(svc, tenant), svc.llm, checkpointer=svc.checkpointer)
    config = thread_config(tenant, dispute_id)
    if not graph.get_state(config).next:
        raise HTTPException(409, "this review has no paused workflow")
    graph.invoke(Command(resume={"action": body.action, "note": body.note}), config)
    return {"dispute_id": dispute_id, "result": graph.get_state(config).values["result"]}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {"services": services is not None}
    if services is not None:
        try:
            with services.pool.tenant_conn("baobab") as conn:
                count = conn.execute("SELECT count(*) AS n FROM clauses").fetchone()["n"]  # pyright: ignore[reportOptionalSubscript]
            checks["clauses_loaded"] = count > 0
        except Exception as exc:  # noqa: BLE001
            checks["database"] = repr(exc)[:200]
            checks["clauses_loaded"] = False
    if not all(v is True for k, v in checks.items() if k != "database"):
        response.status_code = 503
    return checks


try:
    import gradio as gr

    from adjudicate.ui.demo import build_demo

    app = gr.mount_gradio_app(app, build_demo(), path="/demo")
except ImportError:  # the UI is an optional extra
    pass

__all__ = ["app", "json"]
