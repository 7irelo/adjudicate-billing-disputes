"""Tune fraud thresholds on one seed and report on another. Free, with no model involved.

Patterns are jittered per seed (random calls dropped, times shifted), because a screen scored on the
exact rows it was tuned on says nothing. Results are for synthetic patterns only.

    python -m evals.fraud_eval --tune-seed 11 --report-seed 23 --accounts 60
"""

import argparse
import json
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from adjudicate.config import REPO_ROOT
from adjudicate.fraud.screen import DEFAULT, Thresholds, screen
from adjudicate.world.fraud_patterns import (
    add_diaspora_family,
    add_irsf,
    add_low_and_slow,
    add_overseas_client,
    add_pbx_burst,
    add_real_trip,
    add_wangiri,
)
from adjudicate.world.generator import World

FRAUD = {
    "pbx_burst": add_pbx_burst,
    "irsf": add_irsf,
    "wangiri": add_wangiri,
    "low_and_slow": add_low_and_slow,
}
LEGIT = {
    "real_trip": add_real_trip,
    "diaspora_family": add_diaspora_family,
    "overseas_client": add_overseas_client,
}
GRID = {
    "spike_strong_multiple": (5, 8, 12),
    "high_risk_strong_calls": (3, 5, 8),
    "off_hours_burst_calls": (6, 10, 15),
    "sequential_run": (4, 5, 8),
}


def jitter(rows: list[dict], rng: random.Random) -> list[dict]:
    """Drop up to 40% of the disputed month's calls, and move each by up to three hours."""
    keep_rate = rng.uniform(0.6, 1.0)
    out = []
    for row in rows:
        if row["started_at"].month == 5 and rng.random() > keep_rate:
            continue
        shifted = dict(row)
        shifted["started_at"] = row["started_at"] + timedelta(minutes=rng.randint(-180, 180))
        out.append(shifted)
    return out


def sample(pattern: Callable, tenant: str, rng: random.Random) -> tuple[list[dict], list[dict]]:
    world = World(rng.randint(0, 10**9))
    _, sub = world.add_account(tenant)
    pattern(world, tenant, sub)
    calls = jitter([c for c in world.rows["cdrs"] if c["subscription_id"] == sub], rng)
    return (
        [c for c in calls if c["started_at"].month == 5],
        [c for c in calls if c["started_at"].month in (2, 3, 4)],
    )


def build_samples(seed: int, accounts: int) -> dict[str, list[tuple]]:
    rng = random.Random(seed)
    samples: dict[str, list[tuple]] = defaultdict(list)
    for name, pattern in {**FRAUD, **LEGIT}.items():
        for index in range(accounts):
            tenant = ("baobab", "karoo", "fynbos")[index % 3]
            may, baseline = sample(pattern, tenant, rng)
            customer = "business" if tenant == "fynbos" else "consumer"
            samples[name].append((may, baseline, customer))
    return samples


def score(samples: dict[str, list[tuple]], thresholds: Thresholds) -> dict:
    """Per-pattern escalation rate. Low-and-slow is reported, but not tuned for."""
    rates = {
        name: sum(
            screen(may, base, customer, t=thresholds).outcome == "flagged"
            for may, base, customer in group
        )
        / len(group)
        for name, group in samples.items()
    }
    fraud = [rates[n] for n in FRAUD if n in rates]
    legit = [rates[n] for n in LEGIT if n in rates]
    return {
        "per_pattern_flag_rate": {k: round(v, 3) for k, v in rates.items()},
        "fraud_recall": round(sum(fraud) / len(fraud), 3),
        "false_escalation": round(sum(legit) / len(legit), 3),
    }


def tune(samples: dict[str, list[tuple]]) -> Thresholds:
    """Fewest false escalations first, then the highest recall on the non-evasive patterns."""
    tunable = {k: v for k, v in samples.items() if k != "low_and_slow"}
    best, best_key = DEFAULT, None
    for spike in GRID["spike_strong_multiple"]:
        for risk in GRID["high_risk_strong_calls"]:
            for night in GRID["off_hours_burst_calls"]:
                for run in GRID["sequential_run"]:
                    candidate = replace(
                        DEFAULT,
                        spike_strong_multiple=spike,
                        high_risk_strong_calls=risk,
                        off_hours_burst_calls=night,
                        sequential_run=run,
                    )
                    got = score(tunable, candidate)
                    # Prefer the more conservative thresholds on a tie.
                    key = (-got["false_escalation"], got["fraud_recall"], spike, risk, night, run)
                    if best_key is None or key > best_key:
                        best, best_key = candidate, key
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tune-seed", type=int, default=11)
    parser.add_argument("--report-seed", type=int, default=23)
    parser.add_argument("--accounts", type=int, default=30)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "evals/results/summaries/fraud_eval.json"
    )
    args = parser.parse_args()
    tuning = build_samples(args.tune_seed, args.accounts)
    chosen = tune(tuning)
    report = build_samples(args.report_seed, args.accounts)
    result = {
        "note": "Synthetic patterns only. Says nothing about real-world fraud detection.",
        "accounts_per_pattern": args.accounts,
        "tune_seed": args.tune_seed,
        "report_seed": args.report_seed,
        "chosen_thresholds": {k: getattr(chosen, k) for k in GRID},
        "default_on_report_seed": score(report, DEFAULT),
        "chosen_on_tuning_seed": score(tuning, chosen),
        "chosen_on_report_seed": score(report, chosen),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1) + "\n", "utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
