"""The CI gate. Reads a run's summary and rows, compares with baseline.json, and sets the exit code.

Exit 0: pass. Exit 1: regression. Exit 2: the run errored (an outage isn't a regression).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from evals.stats import paired_bootstrap_diff

BASELINE = Path(__file__).parent / "baseline.json"
MAX_INFRA_ERROR_RATE = 0.05


def load_rows(run_dir: Path) -> list[dict]:
    text = (run_dir / "results.jsonl").read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def evaluate(
    summary: dict[str, Any], rows: list[dict], baseline: dict[str, Any]
) -> tuple[int, list[str]]:
    """Return (exit code, reasons). Pure, so each rule has a direct test."""
    if summary.get("infra_error_rate", 0) > MAX_INFRA_ERROR_RATE:
        return 2, [
            f"infra error rate {summary['infra_error_rate']} is above {MAX_INFRA_ERROR_RATE}"
        ]
    if not rows:
        return 2, ["no result rows"]
    if summary.get("meta", {}).get("budget_stopped"):
        return 2, ["the run stopped at its budget cap, so it's incomplete"]

    failures: list[str] = []
    for item in summary.get("zero_tolerance", []):
        failures.append(f"zero tolerance: {item['case_id']}: {item['problems']}")

    for metric, floor in baseline.get("floors", {}).items():
        value = summary["metrics"][metric]["mean"]
        if value < floor:
            failures.append(f"floor: {metric} is {value}, below {floor}")

    # Noise-aware delta: fail only when the whole CI sits below the allowed drop.
    reference = baseline.get("per_case", {})
    allowed = baseline.get("allowed_drop", 0.05)
    for metric in baseline.get("delta_metrics", []):
        pairs = [
            (reference[r["case_id"]][metric], float(r["grade"][metric]))
            for r in rows
            if r["case_id"] in reference and metric in reference[r["case_id"]]
        ]
        if len(pairs) < 2:
            continue
        before, after = zip(*pairs, strict=True)
        diff, low, high = paired_bootstrap_diff(before, after)
        if high < -allowed:
            failures.append(
                f"regression: {metric} moved {diff:+.3f} (95% CI {low:+.3f} to {high:+.3f}), "
                f"below the allowed drop of {allowed}"
            )
    return (1 if failures else 0), failures


def make_baseline(rows: list[dict], reason: str, metrics: list[str], margin: float = 0.05) -> dict:
    """Baseline changes are explicit commits with a reason, so nobody quietly moves the bar."""
    floors = {}
    for metric in metrics:
        values = [float(r["grade"][metric]) for r in rows if r["grade"].get(metric) is not None]
        floors[metric] = round(max(0.0, sum(values) / len(values) - margin), 4)
    return {
        "reason": reason,
        "floors": floors,
        "delta_metrics": metrics,
        "allowed_drop": margin,
        "per_case": {
            r["case_id"]: {
                m: float(r["grade"][m]) for m in metrics if r["grade"].get(m) is not None
            }
            for r in rows
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--write-baseline", metavar="REASON")
    args = parser.parse_args()
    rows = load_rows(args.run_dir)
    if args.write_baseline:
        metrics = ["decision_correct", "clause_recall", "record_recall", "credit_exact"]
        args.baseline.write_text(
            json.dumps(make_baseline(rows, args.write_baseline, metrics), indent=1) + "\n", "utf-8"
        )
        print("baseline written; commit it with the reason")
        sys.exit(0)
    summary = json.loads((args.run_dir / "summary.json").read_text("utf-8"))
    baseline = json.loads(args.baseline.read_text("utf-8")) if args.baseline.exists() else {}
    code, reasons = evaluate(summary, rows, baseline)
    print({0: "PASS", 1: "FAIL", 2: "ERRORED"}[code])
    for reason in reasons:
        print(" -", reason)
    sys.exit(code)
