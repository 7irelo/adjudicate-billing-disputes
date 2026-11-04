"""Should the faithfulness judge be trusted? Compare it with human labels, on real decisions.

Needs an API key, a results run, and data/judge/labels.jsonl labelled by a person BEFORE seeing any
judge output. Each label line: {"case_id": "...", "claim": "...", "label": "supported"}.
Claims are matched to the judge's by exact text first, then by best word overlap.

    python -m evals.judge_calibration evals/results/<run_id> --labels data/judge/labels.jsonl
"""

import argparse
import json
import random
import re
from pathlib import Path

from adjudicate.llm import LLM, AnthropicLLM
from adjudicate.schemas import Decision
from evals.graders.faithfulness import (
    MUTATIONS,
    agreement_report,
    faithfulness_score,
    judge,
    mutate,
)

_WORD = re.compile(r"\w+")


def overlap(a: str, b: str) -> float:
    left, right = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    return len(left & right) / max(len(left | right), 1)


def match_labels(
    human: list[dict], judged: dict[str, list[dict]], minimum: float = 0.5
) -> tuple[list[str], list[str]]:
    """Pair each human claim with the judge's closest. Unmatched human claims count as unsupported
    by the judge, because a judge that skipped a claim hasn't vouched for it."""
    human_labels, judge_labels = [], []
    for item in human:
        candidates = judged.get(item["case_id"], [])
        best = max(candidates, key=lambda c: overlap(c["claim"], item["claim"]), default=None)
        found = best is not None and overlap(best["claim"], item["claim"]) >= minimum
        human_labels.append(item["label"])
        judge_labels.append(best["verdict"] if found and best else "unsupported")
    return human_labels, judge_labels


def mutant_detection(llm: LLM, rows: list[dict], reps: int = 1, seed: int = 0) -> dict[str, dict]:
    """Share of mutants scoring below their faithful original. Reported apart, since it's easier."""
    rng = random.Random(seed)
    results: dict[str, dict] = {}
    for kind in MUTATIONS:
        caught = total = 0
        for row in rows:
            decision = Decision.model_validate(row["decision"])
            clauses = row["clauses"]
            evidence = row["evidence"]
            original, _ = judge(llm, row["text"], decision, clauses, evidence)
            for _ in range(reps):
                try:
                    damaged = mutate(decision, kind, rng)
                except ValueError:
                    continue
                got, _ = judge(llm, row["text"], damaged, clauses, evidence)
                total += 1
                caught += faithfulness_score(got) < faithfulness_score(original)
        results[kind] = {
            "n": total,
            "caught": caught,
            "rate": round(caught / total, 3) if total else None,
        }
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--labels", type=Path, default=Path("data/judge/labels.jsonl"))
    args = parser.parse_args()
    # Without labels there's no agreement figure, but mutants and scores still tell us something.
    labels = (
        [json.loads(x) for x in args.labels.read_text("utf-8").splitlines() if x]
        if args.labels.exists()
        else []
    )
    rows = [
        json.loads(x)
        for x in (args.run_dir / "judge_inputs.jsonl").read_text("utf-8").splitlines()
        if x
    ]
    client = AnthropicLLM()
    judged, cost = {}, 0.0
    for row in rows:
        verdict, spent = judge(
            client,
            row["text"],
            Decision.model_validate(row["decision"]),
            row["clauses"],
            row["evidence"],
        )
        cost += spent
        judged[row["case_id"]] = [c.model_dump() for c in verdict.claims]
    scores = [
        sum(c["verdict"] == "supported" for c in claims) / len(claims) if claims else 0.0
        for claims in judged.values()
    ]
    agreement: dict | str = "No human labels yet, so the judge is advisory and can't gate CI."
    if labels:
        human, judge_labels = match_labels(labels, judged)
        agreement = agreement_report(human, judge_labels)
    report = {
        "agreement": agreement,
        "decisions_judged": len(scores),
        "mean_faithfulness": round(sum(scores) / len(scores), 3) if scores else None,
        "below_one": [
            c for c, claims in judged.items() if any(x["verdict"] != "supported" for x in claims)
        ],
        "mutants": mutant_detection(client, rows[:10]),
        "judge_cost_usd": round(cost, 4),
    }
    (args.run_dir / "judge_calibration.json").write_text(json.dumps(report, indent=1), "utf-8")
    print(json.dumps(report, indent=1))
