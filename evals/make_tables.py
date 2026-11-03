"""Generate README result tables from committed summaries, so the two can't drift apart.

    python -m evals.make_tables --write    # replaces the block between the markers in README.md

A results table appears only when a run is marked reportable: a real model on owner-labelled data.
Until then the README says plainly that there's nothing to report.
"""

import argparse
import json
from pathlib import Path

from adjudicate.config import REPO_ROOT

SUMMARIES = REPO_ROOT / "evals" / "results" / "summaries"
README = REPO_ROOT / "README.md"
START = "<!-- results:start -->"
END = "<!-- results:end -->"

ROWS = [
    ("Decision accuracy", "decision_correct"),
    ("Exact credit match", "credit_exact"),
    ("Exact credit match, given correct eligibility", "credit_exact_given_eligible"),
    ("Clause citation precision", "clause_precision"),
    ("Clause citation recall", "clause_recall"),
    ("Record citation precision", "record_precision"),
    ("Record citation recall", "record_recall"),
]


def results_block(summaries_dir: Path = SUMMARIES) -> str:
    reportable = []
    for path in sorted(summaries_dir.glob("run_*.json")):
        summary = json.loads(path.read_text("utf-8"))
        if summary.get("meta", {}).get("reportable"):
            reportable.append(summary)
    if not reportable:
        return (
            "**There are no model results to report yet.** A number appears here only for a real "
            "model on owner-labelled data."
        )
    latest = reportable[-1]
    meta = latest["meta"]
    lines = [
        f"Commit `{meta['commit']}`, {meta['cases']} cases, model `{meta['decide_model']}`, "
        f"{meta['started'][:10]}. Intervals are 95% bootstrap.",
        "",
        "| Metric | Value | 95% CI |",
        "|---|---:|---|",
    ]
    for label, key in ROWS:
        metric = latest["metrics"][key]
        lines.append(
            f"| {label} | {metric['mean']:.3f} | {metric['ci_low']:.3f} to {metric['ci_high']:.3f} |"
        )
    fraud = latest.get("fraud", {})
    lines += [
        f"| Fraud escalation recall | {fraud.get('escalation_recall')} | |",
        f"| False escalation rate | {fraud.get('false_escalation_rate')} | |",
        f"| Majority-class baseline accuracy | {latest.get('majority_baseline_accuracy')} | |",
    ]
    return "\n".join(lines)


def replace_block(text: str, block: str) -> str:
    start, end = text.index(START), text.index(END)
    return f"{text[: start + len(START)]}\n{block}\n{text[end:]}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    block = results_block()
    if args.write:
        README.write_text(replace_block(README.read_text("utf-8"), block), "utf-8", newline="\n")
    print(block)
