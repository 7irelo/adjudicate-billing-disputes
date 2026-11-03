"""Recompute every headline number from raw result rows."""

from collections import Counter
from statistics import mean
from typing import Any

from evals.graders.deterministic import critical_failures
from evals.stats import bootstrap_ci

RATE_METRICS = [
    "decision_correct",
    "route_correct",
    "clause_precision",
    "clause_recall",
    "record_precision",
    "record_recall",
    "credit_exact",
    "verify_passed",
    "type_correct",
]
CLASSES = ("credit_due", "charge_valid", "needs_review")


def _rate(rows: list[dict], key: str) -> dict[str, float | int]:
    values = [float(r["grade"][key]) for r in rows if r["grade"].get(key) is not None]
    centre, low, high = bootstrap_ci(values)
    return {
        "mean": round(centre, 4),
        "ci_low": round(low, 4),
        "ci_high": round(high, 4),
        "n": len(values),
    }


def _f1(rows: list[dict], label: str) -> float:
    tp = sum(r["grade"]["predicted"] == label and r["grade"]["expected"] == label for r in rows)
    fp = sum(r["grade"]["predicted"] == label and r["grade"]["expected"] != label for r in rows)
    fn = sum(r["grade"]["predicted"] != label and r["grade"]["expected"] == label for r in rows)
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def summarise(rows: list[dict], errors: list[dict], meta: dict[str, Any]) -> dict[str, Any]:
    total = len(rows) + len(errors)
    summary: dict[str, Any] = {
        "meta": meta,
        "rows": len(rows),
        "infra_errors": len(errors),
        "infra_error_rate": round(len(errors) / total, 4) if total else 0.0,
    }
    if not rows:
        return summary
    grades = [r["grade"] for r in rows]
    summary["metrics"] = {m: _rate(rows, m) for m in RATE_METRICS}
    summary["metrics"]["credit_exact_given_eligible"] = _rate(rows, "credit_exact_given_eligible")
    summary["metrics"]["dual_evidence"] = _rate(rows, "dual_evidence")
    summary["macro_f1"] = round(mean(_f1(rows, c) for c in CLASSES), 4)
    majority = Counter(g["expected"] for g in grades).most_common(1)[0]
    summary["majority_baseline_accuracy"] = round(majority[1] / len(grades), 4)
    summary["confusion"] = {
        f"{exp}->{pred}": count
        for (exp, pred), count in sorted(
            Counter((g["expected"], g["predicted"]) for g in grades).items()
        )
    }
    summary["wrongful_credit_rate"] = round(mean(g["wrongful_credit"] for g in grades), 4)
    summary["wrongful_rejection_rate"] = round(mean(g["wrongful_rejection"] for g in grades), 4)
    summary["over_credit_rand"] = {
        "total": sum(g["over_credit_cents"] for g in grades) / 100,
        "worst": max(g["over_credit_cents"] for g in grades) / 100,
    }
    summary["under_credit_rand"] = {
        "total": sum(g["under_credit_cents"] for g in grades) / 100,
        "worst": max(g["under_credit_cents"] for g in grades) / 100,
    }
    fraud = [g for g in grades if g["is_fraud_case"]]
    non_fraud = [g for g in grades if not g["is_fraud_case"]]
    summary["fraud"] = {
        "cases": len(fraud),
        "escalation_recall": round(mean(g["fraud_escalated"] for g in fraud), 4) if fraud else None,
        "false_escalation_rate": round(mean(g["false_escalation"] for g in non_fraud), 4)
        if non_fraud
        else None,
    }
    auto = [g for g in grades if g["routed_to"] == "auto"]
    summary["auto"] = {
        "coverage": round(len(auto) / len(grades), 4),
        "precision": round(mean(g["decision_correct"] and g["credit_exact"] for g in auto), 4)
        if auto
        else None,
    }
    summary["llm_failures"] = dict(Counter(g["llm_failure"] for g in grades if g["llm_failure"]))
    summary["zero_tolerance"] = [
        {"case_id": r["case_id"], "problems": critical_failures(r["grade"])}
        for r in rows
        if critical_failures(r["grade"])
    ]
    summary["cost_usd"] = round(sum(r["cost_usd"] for r in rows), 4)
    latencies = sorted(r["latency_ms"] for r in rows)
    summary["latency_ms"] = {
        "p50": latencies[len(latencies) // 2],
        "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
    }
    summary["served_models"] = dict(Counter(m for r in rows for m in r["served_models"]))
    summary["failed_cases"] = [r["case_id"] for r in rows if not r["grade"]["decision_correct"]]
    return summary
