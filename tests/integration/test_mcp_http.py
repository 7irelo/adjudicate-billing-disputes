"""The real MCP server over HTTP: auth, tenant binding, and parity with in-process calls."""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pytest
import uvicorn

from adjudicate.mcp_client import McpTools
from adjudicate.mcp_server import server as mcp_server
from adjudicate.mcp_server.service import ToolService
from adjudicate.tenancy.auth import issue_token
from adjudicate.world.seed import build_world, manifest

pytestmark = pytest.mark.db
MAY = (date(2026, 5, 1), date(2026, 5, 31))
BODY = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


@pytest.fixture(scope="module")
def scenarios(seeded_db):
    from adjudicate.config import get_settings

    return manifest(build_world(get_settings().world_seed, 2))["scenarios"]


@pytest.fixture(scope="module")
def url(tenant_pool):
    mcp_server._pool = tenant_pool
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(mcp_server.build_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=10)
    mcp_server._pool = None


def test_no_token_is_rejected(url) -> None:
    assert httpx.post(url, json=BODY).status_code == 401


def test_bad_and_expired_tokens_are_rejected(url) -> None:
    for token in ("nonsense", issue_token("baobab", ttl_seconds=-5)):
        response = httpx.post(url, json=BODY, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


def test_http_tools_match_in_process_tools(url, tenant_pool, scenarios) -> None:
    account = scenarios["baobab:duplicate_recurring"]["account_ref"]
    remote, local = McpTools(url, "baobab"), ToolService(tenant_pool, "baobab")
    assert remote.account_exists(account) is True
    assert remote.lookup_invoice(account, *MAY) == local.lookup_invoice(account, *MAY)
    assert remote.search_cdrs(account, *MAY) == local.search_cdrs(account, *MAY, None)
    assert remote.subscription_history(account) == local.subscription_history(account)
    assert remote.screen_fraud(account, *MAY) == local.screen_fraud(account, *MAY)
    assert remote.get_tariff(account, "roaming", MAY[0]) == local.get_tariff(
        account, "roaming", MAY[0]
    )
    assert remote.search_clauses("duplicate charge", 5, MAY[0]) == local.search_clauses(
        "duplicate charge", 5, MAY[0]
    )
    assert remote.classify_dispute("billed twice") == local.classify_dispute("billed twice")
    clause = "baobab-tcs-2026:billing:2.3"
    assert remote.get_clause(clause) == local.get_clause(clause)


def test_token_decides_the_tenant_over_http(url, scenarios) -> None:
    """A karoo token asking for a baobab account sees exactly what a missing account shows."""
    account = scenarios["baobab:duplicate_recurring"]["account_ref"]
    karoo = McpTools(url, "karoo")
    assert karoo.account_exists(account) is False
    assert karoo.lookup_invoice(account, *MAY).record_ids == []
    assert karoo.get_clause("baobab-tcs-2026:billing:2.3") is None


def test_concurrent_tenants_do_not_mix_over_http(url) -> None:
    def check(tenant: str) -> bool:
        hits = McpTools(url, tenant).search_clauses("credit limit", 10, MAY[0])
        return bool(hits) and all(h["clause"]["clause_id"].startswith(tenant) for h in hits)

    jobs = ["baobab", "karoo", "fynbos"] * 8
    with ThreadPoolExecutor(max_workers=6) as executor:
        assert all(executor.map(check, jobs))
