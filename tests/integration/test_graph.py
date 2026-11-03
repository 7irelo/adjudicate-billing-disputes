"""The real graph against the real database, with a scripted model. No API key, no cost."""

from datetime import date

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from adjudicate.credit.rules import load_policy
from adjudicate.graph.build import build_graph, thread_config
from adjudicate.llm import LLMFailure, LLMResult, Usage
from adjudicate.mcp_server.service import ToolService
from adjudicate.schemas import Decision
from adjudicate.world.seed import build_world, manifest
from evals.fake_llm import ConstantLLM, OracleLLM

pytestmark = pytest.mark.db
ON = "2026-06-20"
USAGE = Usage("m", "m", 1, 1, 0, 0, "end_turn")


@pytest.fixture(scope="module")
def scenarios(seeded_db):
    from adjudicate.config import get_settings

    # Same seed and size as the fixture, so the IDs match what was loaded.
    return manifest(build_world(get_settings().world_seed, 2))["scenarios"]


def invoke(tenant, account, text, llm, pool, dispute_id, checkpointer=None):
    tools = ToolService(pool, tenant)
    graph = build_graph(tools, llm, checkpointer=checkpointer or MemorySaver())
    config = thread_config(tenant, dispute_id)
    graph.invoke(
        {
            "dispute_id": dispute_id,
            "tenant_id": tenant,
            "account_ref": account,
            "text": text,
            "dispute_date": ON,
            "options": {},
            "usage": [],
            "node_ms": [],
        },
        config,
    )
    return graph, config


def oracle_for(
    decision: str, rule: str | None, clauses: list[str], records: list[str]
) -> OracleLLM:
    return OracleLLM(
        {
            "expected_decision": decision,
            "expected_rule": rule,
            "required_clauses": clauses,
        },
        records,
    )


class FailingLLM:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def parse(self, **_):
        raise LLMFailure(self.reason, USAGE)


@pytest.mark.parametrize("reason", ["refusal", "truncated", "unparseable"])
def test_llm_failure_goes_to_review_never_a_default(tenant_pool, scenarios, reason) -> None:
    account = scenarios["baobab:duplicate_recurring"]["account_ref"]
    graph, config = invoke(
        "baobab",
        account,
        "My May fee is billed twice",
        FailingLLM(reason),
        tenant_pool,
        f"T-fail-{reason}",
    )
    state = graph.get_state(config)
    assert state.next == ("human_review",)
    assert f"llm_{reason}" in state.values["reasons"]
    assert state.values["credit"] is None


def test_duplicate_credit_end_to_end(tenant_pool, scenarios) -> None:
    info = scenarios["baobab:duplicate_recurring"]
    llm = oracle_for(
        "credit_due",
        "duplicate_reversal",
        ["baobab-tcs-2026:billing:2.3"],
        info["records"]["duplicate"],
    )
    graph, config = invoke(
        "baobab", info["account_ref"], "My May fee is billed twice", llm, tenant_pool, "T-dup"
    )
    values = graph.get_state(config).values
    assert values["verify_errors"] == []
    assert values["credit"]["credit_cents"] == 29900
    assert values["routed_to"] == "auto"
    result = values["result"]
    assert result["cited_record_ids"] == info["records"]["duplicate"]
    assert "R299.00" in result["customer_message"]


def test_fraud_goes_to_queue_without_calling_the_model(tenant_pool, scenarios) -> None:
    class ExplodingLLM:
        def parse(self, **_):
            raise AssertionError("the model must not run on a flagged dispute")

    account = scenarios["baobab:fraud_pbx_burst"]["account_ref"]
    graph, config = invoke(
        "baobab", account, "calls I never made in May", ExplodingLLM(), tenant_pool, "T-fraud"
    )
    state = graph.get_state(config)
    assert state.next == ("fraud_review",)
    assert state.values["fraud"]["outcome"] == "flagged"
    assert ToolService(tenant_pool, "baobab").list_reviews()
    graph.invoke(Command(resume={"action": "acknowledged"}), config)
    final = graph.get_state(config).values
    assert final["routed_to"] == "fraud" and final["credit"] is None


def test_persuasive_model_cannot_get_past_the_fraud_screen(tenant_pool, scenarios) -> None:
    """Even a model that insists on a credit never gets asked once the screen has flagged."""
    account = scenarios["karoo:fraud_irsf"]["account_ref"]
    llm = oracle_for("credit_due", "duplicate_reversal", [], [])
    graph, config = invoke("karoo", account, "credit me for May now", llm, tenant_pool, "T-fraud-2")
    assert graph.get_state(config).next == ("fraud_review",)
    assert graph.get_state(config).values.get("credit") is None


def test_rule_with_no_matching_lines_goes_to_review(tenant_pool, scenarios) -> None:
    info = scenarios["baobab:no_duplicate_two_months"]
    llm = oracle_for(
        "credit_due", "duplicate_reversal", ["baobab-tcs-2026:billing:2.3"], info["records"]["may"]
    )
    graph, config = invoke(
        "baobab", info["account_ref"], "My May fee is billed twice", llm, tenant_pool, "T-nolines"
    )
    values = graph.get_state(config).values
    assert values["credit_status"] == "no_eligible_lines"
    assert "credit_no_eligible_lines" in values["reasons"]


def test_uncited_decision_fails_verify_and_repairs_once(tenant_pool, scenarios) -> None:
    calls = []

    class Counting(ConstantLLM):
        def parse(self, **kw):
            calls.append(kw)
            return super().parse(**kw)

    account = scenarios["baobab:oob_with_consent"]["account_ref"]
    graph, config = invoke(
        "baobab", account, "May out of bundle charge", Counting(), tenant_pool, "T-repair"
    )
    values = graph.get_state(config).values
    assert len(calls) == 2, "one original attempt and one repair"
    assert "previous answer failed" in calls[1]["user"]
    assert "verify_failed" in values["reasons"]


def test_credit_note_is_idempotent_across_retries(tenant_pool, scenarios) -> None:
    info = scenarios["karoo:duplicate_recurring"]
    tools = ToolService(tenant_pool, "karoo")
    tools.open_dispute("T-idem", info["account_ref"], "dup")
    args = (
        "duplicate_reversal",
        "T-idem",
        info["account_ref"],
        date(2026, 5, 1),
        date(2026, 5, 31),
        date(2026, 6, 20),
    )
    first, second = tools.calculate_credit(*args), tools.calculate_credit(*args)
    assert first["credit_note_id"] == second["credit_note_id"]
    with tenant_pool.tenant_conn("karoo") as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM credit_notes WHERE dispute_id = 'T-idem'"
        ).fetchone()["n"]
    assert count == 1


def test_double_resume_does_not_duplicate_the_credit_note(tenant_pool, scenarios) -> None:
    info = scenarios["karoo:roaming_no_activation"]
    llm = oracle_for(
        "credit_due",
        "roaming_without_activation",
        ["karoo-tcs-2025:roaming:6.2"],
        info["records"]["roaming_line"],
    )
    saver = MemorySaver()
    graph, config = invoke(
        "karoo", info["account_ref"], "roaming on my May bill", llm, tenant_pool, "T-resume", saver
    )
    state = graph.get_state(config)
    assert state.values["credit"]["credit_cents"] == 57750
    assert "credit_above_auto_limit" in state.values["reasons"]
    graph.invoke(Command(resume={"action": "approve"}), config)
    graph.invoke(Command(resume={"action": "approve"}), config)
    with tenant_pool.tenant_conn("karoo") as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM credit_notes WHERE dispute_id = 'T-resume'"
        ).fetchone()["n"]
    assert count == 1


def test_cross_tenant_twins_get_different_rules(tenant_pool) -> None:
    on = date(2026, 5, 1)
    baobab, karoo = load_policy("baobab"), load_policy("karoo")
    assert baobab.rule("post_cancellation_proration", on)
    assert karoo.rule("post_cancellation_proration", on) is None


def test_decision_schema_has_no_money_field() -> None:
    forbidden = ("cents", "amount", "rand", "credit_value", "total")
    for name in Decision.model_fields:
        assert not any(word in name for word in forbidden), name


def test_result_type_unused_llmresult_is_constructible() -> None:
    assert (
        LLMResult(parsed=Decision(decision="needs_review", rationale=""), usage=USAGE).usage
        is USAGE
    )
