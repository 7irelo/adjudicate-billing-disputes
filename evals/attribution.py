"""Name the layer behind each failure by swapping in oracles, one at a time."""

from typing import Any

from adjudicate.db import TenantPool
from adjudicate.graph.build import RunOptions
from adjudicate.llm import LLM
from evals.runner import load_manifest, resolve_gold, run_case

ORACLES = ("classifier", "clause_retrieval", "record_retrieval")


def passed(grade: dict) -> bool:
    return grade["decision_correct"] and grade["credit_exact"]


def options_for(oracle: str, case: dict, resolved: dict) -> RunOptions:
    if oracle == "classifier":
        return RunOptions(oracle_type=case["expected_type"])
    if oracle == "clause_retrieval":
        return RunOptions(oracle_clause_ids=case["required_clauses"] + case["acceptable_clauses"])
    if oracle == "record_retrieval":
        # Records are fetched by playbook, so the fix for this layer is the right playbook.
        return RunOptions(
            oracle_type=case["expected_type"], oracle_record_ids=resolved["required_records"]
        )
    raise ValueError(oracle)


def attribute(case: dict, llm: LLM, pool: TenantPool, run_id: str, first: dict) -> dict[str, Any]:
    """Re-run one failed case per oracle. Paid when `llm` is the real model."""
    manifest = load_manifest()
    resolved = resolve_gold(case, manifest)
    fixed_by = []
    for oracle in ORACLES:
        options = options_for(oracle, case, resolved)
        row = run_case(
            case, llm, pool, f"{run_id}-{oracle[:3]}", options=options, manifest=manifest
        )
        if passed(row["grade"]):
            fixed_by.append(oracle)
    grade = first["grade"]
    if fixed_by:
        layer = fixed_by[0]
    elif grade["decision_correct"] and not grade["credit_exact"]:
        layer = _credit_layer(grade)
    else:
        layer = "reasoning"
    return {"case_id": case["case_id"], "layer": layer, "fixed_by": fixed_by}


def _credit_layer(grade: dict) -> str:
    if not grade["rule_correct"]:
        return "credit:wrong_rule"
    if grade["predicted"] != "credit_due":
        return "credit:wrong_eligibility"
    return "credit:scope_or_calculator"


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    from adjudicate.llm import AnthropicLLM
    from evals.runner import load_cases

    parser = argparse.ArgumentParser(description="Paid: three more runs per failed case.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--split", default="dev")
    args = parser.parse_args()
    text = (args.run_dir / "results.jsonl").read_text("utf-8")
    rows = {r["case_id"]: r for r in map(json.loads, text.splitlines())}
    cases = {c["case_id"]: c for c in load_cases(args.split)}
    failed = [c for c, r in rows.items() if not passed(r["grade"])]
    pool = TenantPool()
    results = [
        attribute(cases[c], AnthropicLLM(), pool, f"attr{i}", rows[c]) for i, c in enumerate(failed)
    ]
    (args.run_dir / "attribution.json").write_text(json.dumps(results, indent=1), "utf-8")
    print(json.dumps(results, indent=1))
