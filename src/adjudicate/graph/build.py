"""The dispute workflow. One LLM call decides eligibility; everything else is code.

intake -> classify -> gather_evidence -> fraud_screen -+- flagged -> fraud_review -> finalise
                                                       +-> decide -> verify -+- repair -> decide
                                                                             +-> calculate_credit
                                                                                  -> confidence_gate
                                                                                  -> human_review?
                                                                                  -> finalise
"""

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from importlib import resources
from operator import add
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from adjudicate.config import Settings, get_settings
from adjudicate.credit.money import rand
from adjudicate.credit.rules import load_policy
from adjudicate.graph.calibrator import calibration_features, load_calibrator
from adjudicate.graph.gate import GateInput, gate_reasons
from adjudicate.graph.intake import run_intake
from adjudicate.graph.playbooks import (
    clause_queries,
    evidence_period,
    fetch_one,
    gather_everything,
    gather_records,
)
from adjudicate.graph.verify import verify_decision
from adjudicate.llm import LLM, LLMFailure, Usage
from adjudicate.mcp_server.service import Tools
from adjudicate.observability.tracing import record_generation, span, trace_id_for
from adjudicate.schemas import (
    Classification,
    Clause,
    CreditComputation,
    Decision,
    DisputeResult,
    EvidenceSet,
    FraudScreen,
    RecordCitation,
)

SYSTEM_PROMPT = (
    resources.files("adjudicate.prompts").joinpath("decide.md").read_text(encoding="utf-8")
)
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]
CLAUSE_K = 8
MAX_REPAIRS = 1
MAX_FOLLOW_UPS = 1


@dataclass
class RunOptions:
    """Oracle inputs replace one layer at a time for failure attribution. Default: none."""

    oracle_type: str | None = None
    oracle_clause_ids: list[str] | None = None
    oracle_record_ids: list[str] | None = None
    hide_records: bool = False  # mechanism check: scores must fall when this is on
    extra: dict[str, Any] = field(default_factory=dict)


class DisputeState(TypedDict, total=False):
    dispute_id: str
    tenant_id: str
    account_ref: str
    text: str
    dispute_date: str
    options: dict
    period_start: str
    period_end: str
    evidence_start: str
    injection_suspected: bool
    claimed_cents: list[int]
    classification: dict
    clauses: list[dict]
    clause_scores: list[float]
    evidence: list[dict]
    fraud: dict
    decision: dict | None
    llm_failure: str | None
    verify_errors: list[str]
    repairs: int
    follow_ups: int
    p_correct: float | None
    features: dict
    credit_status: str | None
    credit: dict | None
    credit_note_id: str | None
    auto_credit_limit_cents: int | None
    reasons: list[str]
    routed_to: str
    review_id: str | None
    resolution: dict | None
    result: dict
    fallback_served: bool
    usage: Annotated[list[dict], add]
    node_ms: Annotated[list[list], add]


def _timed(name: str, func):
    def wrapper(state: DisputeState) -> dict:
        began = time.perf_counter()
        with span(name, state):
            out = func(state)
        out = dict(out or {})
        out["node_ms"] = [[name, int((time.perf_counter() - began) * 1000)]]
        return out

    return wrapper


def expand_citations(evidence: list[EvidenceSet], citations: list[RecordCitation]) -> list[str]:
    """Code, not the model, turns cited handles into record IDs."""
    by_handle = {e.handle: e for e in evidence}
    ids: list[str] = []
    for citation in citations:
        found = by_handle.get(citation.handle)
        if found:
            ids.extend(citation.record_ids or found.record_ids)
    return sorted(set(ids))


def customer_message(
    tenant_name: str,
    decision: Decision | None,
    credit: dict | None,
    route: str,
    resolution: dict | None = None,
) -> str:
    """Templated, so the model's text can never state a credit amount to a customer."""
    action = (resolution or {}).get("action")
    if route == "human" and action == "approve" and decision and decision.decision == "credit_due":
        amount = rand(credit["credit_cents"]) if credit else None
        if amount:
            return (
                f"A {tenant_name} billing specialist approved a credit of {amount}. "
                "If you are unhappy with this outcome, you may escalate it."
            )
    if (
        route == "human"
        and action == "approve"
        and decision
        and decision.decision == "charge_valid"
    ):
        return (
            f"A {tenant_name} billing specialist reviewed the charge and found it correct under "
            "your agreement. If you are unhappy with this outcome, you may escalate it."
        )
    if route == "fraud":
        return (
            f"{tenant_name} is investigating this dispute with its fraud team. No credit decision "
            "has been made yet. We will contact you with the outcome."
        )
    if route == "human" or decision is None:
        return (
            f"A {tenant_name} billing specialist will review this dispute and contact you. "
            "If you are unhappy with the final outcome, you may escalate it."
        )
    if decision.decision == "credit_due" and credit:
        return (
            f"{tenant_name} has prepared a credit of {rand(credit['credit_cents'])} for this "
            "dispute. If you are unhappy with this outcome, you may escalate it."
        )
    return (
        f"{tenant_name} reviewed the charge and found it correct under your agreement. "
        "If you are unhappy with this outcome, you may escalate it."
    )


def build_user_prompt(state: DisputeState, policy, errors: list[str] | None = None) -> str:
    rules_on = date.fromisoformat(state["period_start"])
    clauses = [
        {"clause_id": c["clause_id"], "section": " > ".join(c["path"][1:]), "text": c["text"]}
        for c in state.get("clauses", [])
    ]
    evidence = [EvidenceSet.model_validate(e).for_model() for e in state.get("evidence", [])]
    rules = [
        {
            "rule_id": r.rule_id,
            "applies_by": r.method,
            "authorising_clauses": r.authorising_clauses,
            "dispute_window_days": r.dispute_window_days,
        }
        for r in policy.rules_on(rules_on)
    ]
    parts = [
        f"OPERATOR: {policy.name}",
        f"DISPUTE DATE: {state['dispute_date']}",
        f"DISPUTED PERIOD: {state['period_start']} to {state['period_end']}",
        f"CLASSIFIED TYPE: {state['classification']['dispute_type']}",
        f"AVAILABLE RULES:\n{json.dumps(rules, indent=1)}",
        f"CLAUSES:\n{json.dumps(clauses, indent=1)}",
        f"RECORDS:\n{json.dumps(evidence, indent=1, default=str)}",
        f"<dispute>\n{state['text']}\n</dispute>",
    ]
    fraud = state.get("fraud")
    if fraud and fraud["outcome"] == "weak":
        parts.insert(5, f"FRAUD SCREEN (weak signal):\n{json.dumps(fraud, indent=1)}")
    if errors:
        parts.append(
            "Your previous answer failed these checks. Fix them, or use needs_review:\n- "
            + "\n- ".join(errors)
        )
    return "\n\n".join(parts)


def build_graph(tools: Tools, llm: LLM, settings: Settings | None = None, checkpointer=None):
    cfg = settings or get_settings()

    def intake(state: DisputeState) -> dict:
        policy = load_policy(state["tenant_id"])
        got = run_intake(state["text"], date.fromisoformat(state["dispute_date"]), policy.name)
        tools.open_dispute(state["dispute_id"], state["account_ref"], got.text)
        return {
            "text": got.text,
            "claimed_cents": got.claimed_cents,
            "period_start": got.period_start.isoformat(),
            "period_end": got.period_end.isoformat(),
            "evidence_start": got.period_start.isoformat() if got.period_explicit else "",
            "injection_suspected": got.injection_suspected,
            "repairs": 0,
            "follow_ups": 0,
            "verify_errors": [],
        }

    def classify(state: DisputeState) -> dict:
        oracle = state.get("options", {}).get("oracle_type")
        if oracle:
            result = Classification(
                dispute_type=oracle, urgency="routine", confidence=1.0, model="oracle"
            )
        else:
            result = tools.classify_dispute(state["text"])
        return {"classification": result.model_dump()}

    def gather_evidence(state: DisputeState) -> dict:
        kind = state["classification"]["dispute_type"]
        options = state.get("options", {})
        period_start = date.fromisoformat(state["period_start"])
        period_end = date.fromisoformat(state["period_end"])
        explicit = bool(state.get("evidence_start"))
        start, end = evidence_period(
            kind, period_start, period_end, explicit, date.fromisoformat(state["dispute_date"])
        )

        if options.get("oracle_clause_ids") is not None:
            found = [tools.get_clause(c) for c in options["oracle_clause_ids"]]
            clauses = [c.model_dump(mode="json") for c in found if c]
            scores = [1.0] * len(clauses)
        else:
            merged: dict[str, tuple[dict, float]] = {}
            for query in clause_queries(kind, state["text"]):
                for hit in tools.search_clauses(query, CLAUSE_K, period_start):
                    clause = hit["clause"]
                    existing = merged.get(clause["clause_id"])
                    if existing is None or hit["score"] > existing[1]:
                        merged[clause["clause_id"]] = (clause, hit["score"])
            ranked = sorted(merged.values(), key=lambda pair: (-pair[1], pair[0]["clause_id"]))
            clauses = [pair[0] for pair in ranked[: CLAUSE_K * 2]]
            scores = [pair[1] for pair in ranked[: CLAUSE_K * 2]]

        if options.get("oracle_record_ids") is not None:
            evidence = gather_everything(tools, state["account_ref"], start, end)
        else:
            evidence = gather_records(tools, kind, state["account_ref"], start, end)
        if options.get("hide_records"):
            evidence = []
        return {
            "clauses": clauses,
            "clause_scores": scores,
            "evidence": [e.model_dump(mode="json") for e in evidence],
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
        }

    def fraud_screen(state: DisputeState) -> dict:
        start = date.fromisoformat(state["period_start"])
        end = date.fromisoformat(state["period_end"])
        result: FraudScreen = tools.screen_fraud(state["account_ref"], start, end)
        return {"fraud": result.model_dump()}

    def route_after_fraud(state: DisputeState) -> str:
        return "fraud_review" if state["fraud"]["outcome"] == "flagged" else "decide"

    def decide(state: DisputeState) -> dict:
        policy = load_policy(state["tenant_id"])
        user = build_user_prompt(state, policy, state.get("verify_errors") or None)
        try:
            result = llm.parse(
                model=cfg.decide_model,
                system=SYSTEM_PROMPT,
                user=user,
                schema=Decision,
                effort=cfg.decide_effort,
            )
        except LLMFailure as failure:
            usage = [asdict(failure.usage)] if failure.usage else []
            if failure.usage:
                record_generation(
                    state["dispute_id"],
                    state["tenant_id"],
                    usage[0],
                    failure.usage.cost_usd,
                    PROMPT_HASH,
                )
            return {"decision": None, "llm_failure": failure.reason, "usage": usage}
        record_generation(
            state["dispute_id"],
            state["tenant_id"],
            asdict(result.usage),
            result.usage.cost_usd,
            PROMPT_HASH,
        )
        return {
            "decision": result.parsed.model_dump(),
            "llm_failure": None,
            "usage": [asdict(result.usage)],
            "fallback_served": result.usage.served_model != result.usage.requested_model,
        }

    def verify(state: DisputeState) -> dict:
        if state.get("decision") is None:
            return {"verify_errors": []}
        errors = verify_decision(
            Decision.model_validate(state["decision"]),
            [Clause.model_validate(c) for c in state.get("clauses", [])],
            [EvidenceSet.model_validate(e) for e in state.get("evidence", [])],
            load_policy(state["tenant_id"]),
            date.fromisoformat(state["period_start"]),
        )
        return {"verify_errors": errors, "repairs": state.get("repairs", 0)}

    def route_after_verify(state: DisputeState) -> str:
        if state.get("verify_errors") and state.get("repairs", 0) < MAX_REPAIRS:
            return "repair"
        decision = state.get("decision")
        if (
            decision
            and decision.get("follow_up")
            and not decision.get("evidence_sufficient", True)
            and cfg.follow_up_enabled
            and state.get("follow_ups", 0) < MAX_FOLLOW_UPS
        ):
            return "follow_up"
        return "calculate_credit"

    def follow_up(state: DisputeState) -> dict:
        """Fetch the one record set the model asked for, then let it decide again."""
        got = fetch_one(
            tools,
            (state.get("decision") or {})["follow_up"],
            state["account_ref"],
            date.fromisoformat(state["period_start"]),
            date.fromisoformat(state["period_end"]),
        )
        known = {e["handle"] for e in state.get("evidence", [])}
        extra = [] if got.handle in known else [got.model_dump(mode="json")]
        return {
            "evidence": [*state.get("evidence", []), *extra],
            "follow_ups": state.get("follow_ups", 0) + 1,
            "verify_errors": [],
        }

    def repair(state: DisputeState) -> dict:
        return {"repairs": state.get("repairs", 0) + 1}

    def calculate_credit(state: DisputeState) -> dict:
        decision = state.get("decision")
        if not decision or decision["decision"] != "credit_due" or state.get("verify_errors"):
            return {"credit": None, "credit_status": None}
        outcome = tools.calculate_credit(
            decision["credit_rule_id"],
            state["dispute_id"],
            state["account_ref"],
            date.fromisoformat(state["period_start"]),
            date.fromisoformat(state["period_end"]),
            date.fromisoformat(state["dispute_date"]),
        )
        if outcome["status"] != "ok":
            return {"credit": None, "credit_status": outcome["status"]}
        return {
            "credit": outcome["computation"],
            "credit_status": "ok",
            "credit_note_id": outcome["credit_note_id"],
            "auto_credit_limit_cents": outcome["auto_credit_limit_cents"],
        }

    def confidence_gate(state: DisputeState) -> dict:
        decision = state.get("decision")
        credit = state.get("credit")
        evidence = [EvidenceSet.model_validate(e) for e in state.get("evidence", [])]
        reasons = gate_reasons(
            GateInput(
                decision=Decision.model_validate(decision) if decision else None,
                credit=CreditComputation.model_validate(credit) if credit else None,
                auto_credit_limit_cents=state.get("auto_credit_limit_cents"),
                fraud=FraudScreen.model_validate(state["fraud"]) if state.get("fraud") else None,
                verify_errors=state.get("verify_errors", []),
                llm_failure=state.get("llm_failure"),
                credit_status=state.get("credit_status"),
                injection_suspected=state.get("injection_suspected", False),
                evidence_empty=not evidence
                or not any(e.record_ids for e in evidence if e.record_type == "invoice_line"),
                no_clauses=not state.get("clauses"),
                fallback_served=state.get("fallback_served", False),
                auto_finalise_rejections=cfg.auto_finalise_rejections,
            )
        )
        features = calibration_features(state)
        calibrator = load_calibrator()
        p_correct = calibrator.probability(features) if calibrator else None
        # Stage 2 only ever narrows what stays automatic. It can't override a hard rule.
        if calibrator and not reasons and (p_correct or 0.0) < calibrator.threshold:
            reasons.append("below_calibrated_threshold")
        return {
            "reasons": reasons,
            "routed_to": "human" if reasons else "auto",
            "p_correct": p_correct,
            "features": features,
        }

    def route_after_gate(state: DisputeState) -> str:
        return "human_review" if state["routed_to"] == "human" else "finalise"

    def fraud_review(state: DisputeState) -> dict:
        signals = [s["name"] for s in state["fraud"]["signals"]]
        reasons = [f"fraud_signal:{name}" for name in signals]
        review_id = tools.escalate_to_human(state["dispute_id"], "fraud", reasons)
        resolution = interrupt({"review_id": review_id, "queue": "fraud", "reasons": reasons})
        tools.resolve_review(review_id, resolution)
        return {
            "decision": None,
            "credit": None,
            "reasons": reasons,
            "routed_to": "fraud",
            "review_id": review_id,
            "resolution": resolution,
        }

    def human_review(state: DisputeState) -> dict:
        review_id = tools.escalate_to_human(state["dispute_id"], "human", state["reasons"])
        resolution = interrupt(
            {"review_id": review_id, "queue": "human", "reasons": state["reasons"]}
        )
        tools.resolve_review(review_id, resolution)
        return {"review_id": review_id, "resolution": resolution}

    def finalise(state: DisputeState) -> dict:
        policy = load_policy(state["tenant_id"])
        decision = Decision.model_validate(state["decision"]) if state.get("decision") else None
        evidence = [EvidenceSet.model_validate(e) for e in state.get("evidence", [])]
        citations = decision.record_citations if decision else []
        usages = [Usage(**u) for u in state.get("usage", [])]
        result = DisputeResult(
            dispute_id=state["dispute_id"],
            decision=decision,
            credit=CreditComputation.model_validate(state["credit"])
            if state.get("credit")
            else None,
            routed_to=state["routed_to"],  # pyright: ignore[reportArgumentType]
            gate_reasons=state.get("reasons", []),
            dispute_type=state["classification"]["dispute_type"],
            urgency=state["classification"]["urgency"],
            cited_record_ids=expand_citations(evidence, citations),
            cited_clause_ids=[c.clause_id for c in decision.clause_citations] if decision else [],
            customer_message=customer_message(
                policy.name,
                decision,
                state.get("credit"),
                state["routed_to"],
                state.get("resolution"),
            ),
            review_id=state.get("review_id"),
            resolution=state.get("resolution"),
            trace_id=trace_id_for(state["dispute_id"]),
            p_correct=state.get("p_correct"),
            cost_usd=sum(u.cost_usd for u in usages),
            latency_ms=sum(ms for _, ms in state.get("node_ms", [])),
        )
        dumped = result.model_dump(mode="json")
        tools.save_result(state["dispute_id"], "closed", dumped)
        return {"result": dumped}

    graph = StateGraph(DisputeState)
    nodes = {
        "intake": intake,
        "classify": classify,
        "gather_evidence": gather_evidence,
        "fraud_screen": fraud_screen,
        "decide": decide,
        "verify": verify,
        "repair": repair,
        "follow_up": follow_up,
        "calculate_credit": calculate_credit,
        "confidence_gate": confidence_gate,
        "fraud_review": fraud_review,
        "human_review": human_review,
        "finalise": finalise,
    }
    for name, func in nodes.items():
        graph.add_node(name, _timed(name, func))
    graph.set_entry_point("intake")
    graph.add_edge("intake", "classify")
    graph.add_edge("classify", "gather_evidence")
    graph.add_edge("gather_evidence", "fraud_screen")
    graph.add_conditional_edges(
        "fraud_screen", route_after_fraud, {"fraud_review": "fraud_review", "decide": "decide"}
    )
    graph.add_edge("decide", "verify")
    graph.add_conditional_edges(
        "verify",
        route_after_verify,
        {"repair": "repair", "follow_up": "follow_up", "calculate_credit": "calculate_credit"},
    )
    graph.add_edge("repair", "decide")
    graph.add_edge("follow_up", "decide")
    graph.add_edge("calculate_credit", "confidence_gate")
    graph.add_conditional_edges(
        "confidence_gate",
        route_after_gate,
        {"human_review": "human_review", "finalise": "finalise"},
    )
    graph.add_edge("human_review", "finalise")
    graph.add_edge("fraud_review", "finalise")
    graph.add_edge("finalise", END)
    return graph.compile(checkpointer=checkpointer)


def thread_config(tenant_id: str, dispute_id: str) -> RunnableConfig:
    """The tenant is part of the thread ID, so one tenant's API can't resume another's thread."""
    return {"configurable": {"thread_id": f"{tenant_id}:{dispute_id}"}}
