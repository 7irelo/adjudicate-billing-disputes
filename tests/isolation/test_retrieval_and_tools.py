"""Scoping above the database: retrieval, tools, tokens and canaries."""

import json
import time
from datetime import date

import jwt
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adjudicate.config import get_settings
from adjudicate.mcp_server.service import ToolService
from adjudicate.retrieval.pipeline import (
    Bm25Index,
    ChunkHit,
    clear_cache,
    hydrate_chunks,
    search_clauses,
)
from adjudicate.tenancy.auth import (
    AuthError,
    demo_api_key,
    issue_token,
    tenant_from_api_key,
    verify_token,
)

pytestmark = pytest.mark.db

CANARIES = {
    "baobab": "BAOBAB-CANARY-7741",
    "karoo": "KAROO-CANARY-3392",
    "fynbos": "FYNBOS-CANARY-5518",
}
ON = date(2026, 5, 1)


@pytest.mark.parametrize("tenant", CANARIES)
def test_search_returns_only_own_and_global_clauses(tenant_pool, tenant) -> None:
    clear_cache()
    with tenant_pool.tenant_conn(tenant) as conn:
        hits = search_clauses(conn, tenant, "cancel notice period credit roaming billing", k=50)
    assert hits
    for hit in hits:
        assert hit.clause.clause_id.startswith(tenant) or hit.clause.scope == "global"


def test_same_query_different_tenants_differs(tenant_pool) -> None:
    results = {}
    for tenant in ("karoo", "fynbos"):
        with tenant_pool.tenant_conn(tenant) as conn:
            hits = search_clauses(conn, tenant, "cancellation notice", k=5)
        results[tenant] = {h.clause.clause_id for h in hits}
    assert results["karoo"].isdisjoint(results["fynbos"])


def test_effective_date_selects_the_edition_in_force(tenant_pool) -> None:
    clear_cache()
    with tenant_pool.tenant_conn("baobab") as conn:
        old = search_clauses(conn, "baobab", "bill-shock threshold", k=10, as_of=date(2026, 3, 1))
        new = search_clauses(conn, "baobab", "bill-shock threshold", k=10, as_of=date(2026, 5, 1))
    assert all(
        "2026" not in h.clause.clause_id.split(":")[0] for h in old if h.clause.scope == "tenant"
    )
    assert all(
        "2025" not in h.clause.clause_id.split(":")[0] for h in new if h.clause.scope == "tenant"
    )


def test_regulation_not_yet_in_force_is_not_retrieved(tenant_pool) -> None:
    with tenant_pool.tenant_conn("baobab") as conn:
        before = search_clauses(
            conn, "baobab", "out-of-bundle usage charges opt in", k=30, as_of=ON
        )
        after = search_clauses(
            conn, "baobab", "out-of-bundle usage charges opt in", k=30, as_of=date(2027, 2, 1)
        )
    assert not any(h.clause.scope == "global" for h in before)
    assert any(h.clause.scope == "global" for h in after)


def test_poisoned_index_returns_nothing(tenant_pool, admin_conn) -> None:
    """Tenant A's index is corrupted with tenant B's chunks. RLS on hydration is the backstop."""
    rows = admin_conn.execute(
        "SELECT chunk_id, match_text FROM chunks WHERE tenant_id = 'karoo' AND strategy = 'D'"
    ).fetchall()
    poisoned = Bm25Index([r[0] for r in rows], [r[1] for r in rows])

    def factory(_conn, _tenant, _strategy):
        return poisoned

    with tenant_pool.tenant_conn("baobab") as conn:
        hits = search_clauses(conn, "baobab", "cancellation notice", k=10, index_factory=factory)
        direct = hydrate_chunks(conn, [ChunkHit(rows[0][0], 1.0)], None)
    assert hits == [] and direct == []


def test_no_tool_has_a_tenant_parameter() -> None:
    """The model can't even express a request for another tenant."""
    import asyncio

    from adjudicate.mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert len(tools) >= 10
    for tool in tools:
        assert not any("tenant" in name for name in tool.input_schema.get("properties", {})), (
            tool.name
        )


def test_no_tool_can_issue_a_credit() -> None:
    import asyncio

    from adjudicate.mcp_server.server import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert not {n for n in names if "issue" in n or "refund" in n or "update_invoice" in n}


@pytest.fixture(scope="module")
def foreign_refs(seeded_db):
    from adjudicate.config import get_settings as gs
    from adjudicate.world.seed import build_world, manifest

    scenarios = manifest(build_world(gs().world_seed, 2))["scenarios"]
    return [v["account_ref"] for k, v in scenarios.items() if k.startswith("karoo:")]


def _empty_view(service: ToolService, account: str) -> dict:
    period = (date(2026, 1, 1), date(2026, 6, 30))
    return {
        "exists": service.account_exists(account),
        "invoice": service.lookup_invoice(account, *period).summary,
        "cdrs": service.search_cdrs(account, *period).record_ids,
        "history": service.subscription_history(account).summary,
        "consents": service.list_consents(account).summary,
        "credit": service.calculate_credit(
            "duplicate_reversal", "T-parity", account, *period, date(2026, 6, 20)
        ).get("status"),
    }


def test_foreign_account_looks_exactly_like_a_missing_one(tenant_pool, foreign_refs) -> None:
    service = ToolService(tenant_pool, "baobab")
    foreign = _empty_view(service, foreign_refs[0])
    missing = _empty_view(service, "A-DOES-NOT-EXIST")
    assert foreign == missing
    assert foreign["exists"] is False


@given(st.integers(0, 10_000))
@settings(
    max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_any_foreign_id_returns_nothing(tenant_pool, foreign_refs, index) -> None:
    account = foreign_refs[index % len(foreign_refs)]
    service = ToolService(tenant_pool, "baobab")
    assert service.lookup_invoice(account, date(2025, 1, 1), date(2026, 12, 31)).record_ids == []
    assert service.subscription_history(account).record_ids == []


def test_token_binds_the_tenant_and_round_trips() -> None:
    assert verify_token(issue_token("karoo")) == "karoo"


def test_expired_token_is_rejected() -> None:
    with pytest.raises(AuthError):
        verify_token(issue_token("karoo", ttl_seconds=-5))


def test_tampered_token_is_rejected() -> None:
    token = issue_token("karoo")
    header, payload, signature = token.split(".")
    other = issue_token("baobab").split(".")[1]
    with pytest.raises(AuthError):
        verify_token(f"{header}.{other}.{signature}")
    assert payload != other


def test_wrong_secret_and_wrong_audience_are_rejected() -> None:
    now = int(time.time())
    good = {"sub": "karoo", "aud": "adjudicate-mcp", "exp": now + 60}
    with pytest.raises(AuthError):
        verify_token(jwt.encode(good, "another-secret-another-secret-another", algorithm="HS256"))
    wrong = {**good, "aud": "someone-else"}
    with pytest.raises(AuthError):
        verify_token(jwt.encode(wrong, get_settings().token_secret, algorithm="HS256"))


def test_alg_none_is_rejected() -> None:
    forged = jwt.encode(
        {"sub": "karoo", "aud": "adjudicate-mcp", "exp": int(time.time()) + 60},
        "",
        algorithm="none",
    )
    with pytest.raises(AuthError):
        verify_token(forged)


def test_api_keys() -> None:
    assert tenant_from_api_key(demo_api_key("fynbos")) == "fynbos"
    for bad in ("", "fynbos", "fynbos.wrong", "mallory." + "0" * 32):
        with pytest.raises(AuthError):
            tenant_from_api_key(bad)


@pytest.mark.parametrize("tenant", CANARIES)
def test_tool_output_has_no_other_tenants_canaries(tenant_pool, tenant) -> None:
    """Each tenant's documents hold a unique string. None may appear in another tenant's output."""
    service = ToolService(tenant_pool, tenant)
    blobs = []
    for query in ("fictional operator reference marker", "cancellation", "roaming", "credit limit"):
        blobs.append(json.dumps(service.search_clauses(query, k=50, as_of=None)))
    with tenant_pool.tenant_conn(tenant) as conn:
        for row in conn.execute("SELECT full_text FROM documents").fetchall():
            blobs.append(row["full_text"])
    text = "\n".join(blobs)
    assert CANARIES[tenant] in text, "own canary missing, so this test could pass vacuously"
    for other, canary in CANARIES.items():
        if other != tenant:
            assert canary not in text
