"""Model and effort sweep on `decide`, and the oracle-type ablation. Paid, with a budget per arm.

python -m evals.sweep --models claude-opus-5-5,claude-sonnet-5-5 --efforts medium,high
python -m evals.sweep --ablation          # how much damage does a wrong dispute type do?
"""

import argparse
import json
import os
from pathlib import Path

from adjudicate.config import get_settings
from adjudicate.db import TenantPool
from adjudicate.graph.build import RunOptions
from evals.report import summarise
from evals.runner import RESULTS, load_cases, run_suite


def arm(
    cases: list[dict], budget: float, model: str, effort: str, options: RunOptions | None = None
) -> dict:
    os.environ["ADJUDICATE_DECIDE_MODEL"] = model
    os.environ["ADJUDICATE_DECIDE_EFFORT"] = effort
    get_settings.cache_clear()
    suite = run_suite(cases, "anthropic", TenantPool(), budget_usd=budget, options=options)
    return summarise(suite["rows"], suite["errors"], suite["meta"])


def headline(summary: dict) -> dict:
    metrics = summary.get("metrics", {})
    return {
        "decision_accuracy": metrics.get("decision_correct"),
        "credit_exact": metrics.get("credit_exact"),
        "cost_usd": summary.get("cost_usd"),
        "latency_ms": summary.get("latency_ms"),
        "served_models": summary.get("served_models"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5")
    parser.add_argument("--efforts", default="medium,high")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--budget-per-arm", type=float, default=10.0)
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--out", type=Path, default=RESULTS / "sweep.json")
    args = parser.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("The sweep calls the paid API. Set ANTHROPIC_API_KEY first.")
    cases = load_cases("dev", args.limit)
    settings = get_settings()
    results: dict[str, dict] = {}
    if args.ablation:
        results["predicted_type"] = headline(
            arm(cases, args.budget_per_arm, settings.decide_model, settings.decide_effort)
        )
        accuracy = []
        for case in cases:
            options = RunOptions(oracle_type=case["expected_type"])
            suite = run_suite([case], "anthropic", TenantPool(), budget_usd=2.0, options=options)
            accuracy.extend(suite["rows"])
        summary = summarise(accuracy, [], {"note": "oracle type per case"})
        results["oracle_type"] = headline(summary)
    else:
        for model in args.models.split(","):
            for effort in args.efforts.split(","):
                results[f"{model}/{effort}"] = headline(
                    arm(cases, args.budget_per_arm, model, effort)
                )
    args.out.write_text(json.dumps(results, indent=1), "utf-8")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
