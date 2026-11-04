"""Generate training text with the LLM, sampled over a scenario grid and split by scenario seed.

Paraphrases of one scenario can't straddle train and val, because the split happens on the seed.
The human-written test set is never generated here: data/classifier/test_human.jsonl must be typed
by a person before any model is trained, and it's the only score that gets reported.
"""

import argparse
import hashlib
import json
import random
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from adjudicate.classify.baseline import read_jsonl  # noqa: F401  re-exported for notebooks
from adjudicate.config import REPO_ROOT, get_settings
from adjudicate.llm import LLM, default_llm

TYPES = [
    "roaming",
    "international",
    "cancellation",
    "double_billing",
    "out_of_bundle",
    "suspected_fraud",
    "other",
]
URGENCY = ["routine", "soon", "urgent", "critical"]
PERSONAS = [
    "a pensioner on a prepaid-to-contract upgrade",
    "a student",
    "a small shop owner",
    "an office manager at a business with a PBX",
    "a frequent traveller",
    "a parent paying for a teenager",
]
REGISTERS = [
    "formal",
    "plain",
    "angry",
    "rushed with typos",
    "code-switching with some Afrikaans or isiZulu words",
]
LENGTHS = ["one short sentence", "two sentences", "a short paragraph"]

SYSTEM = (
    "You write realistic, fictional customer complaints to a South African telecom, for training a "
    "classifier. Use South African usage where natural: rands like R1,200, 'debit order', 'airtime', "
    "'contract'. Never use a real person's name, a real phone number or a real operator's name. "
    "Each variant must word the complaint differently, and must not name the category."
)


class _Batch(BaseModel):
    variants: list[str]


def scenario_grid(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    grid = []
    for i in range(count):
        grid.append(
            {
                "seed": i,
                "type": TYPES[i % len(TYPES)],
                "urgency": rng.choice(URGENCY),
                "persona": rng.choice(PERSONAS),
                "register": rng.choice(REGISTERS),
                "length": rng.choice(LENGTHS),
            }
        )
    return grid


def split_of(scenario_seed: int) -> str:
    bucket = int(hashlib.sha256(str(scenario_seed).encode()).hexdigest(), 16) % 10
    return "val" if bucket == 0 else "train"


def _one(llm: LLM, s: dict, variants: int) -> tuple[list[dict], float]:
    user = (
        f"Write {variants} variants of a complaint of type '{s['type']}' with urgency "
        f"'{s['urgency']}', from {s['persona']}, in a {s['register']} register, as "
        f"{s['length']}."
    )
    result = llm.parse(
        model=get_settings().fast_model,
        system=SYSTEM,
        user=user,
        schema=_Batch,
        max_tokens=2000,
    )
    rows = [
        {
            "text": text,
            "type": s["type"],
            "urgency": s["urgency"],
            "scenario_seed": s["seed"],
            "provenance": "llm_generated",
        }
        for text in result.parsed.variants  # pyright: ignore[reportAttributeAccessIssue]
    ]
    return rows, result.usage.cost_usd


def generate(
    llm: LLM, scenarios: list[dict], variants: int = 3, workers: int = 8, budget_usd: float = 2.0
) -> tuple[list[dict], float, int]:
    """Returns (rows, cost, failures). Stops submitting once the budget is reached."""
    rows: list[dict] = []
    spent = 0.0
    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for start in range(0, len(scenarios), workers * 4):
            if spent >= budget_usd:
                break
            batch = scenarios[start : start + workers * 4]
            futures = [executor.submit(_one, llm, s, variants) for s in batch]
            for future in futures:
                try:
                    got, cost = future.result()
                except Exception:  # noqa: BLE001  one bad call shouldn't lose the whole run
                    failures += 1
                    continue
                rows.extend(got)
                spent += cost
    return rows, spent, failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calls the paid API. Haiku, about $0.10/$0.50 per MTok."
    )
    parser.add_argument("--scenarios", type=int, default=1100)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--budget-usd", type=float, default=2.0)
    args = parser.parse_args()
    out = REPO_ROOT / "data" / "classifier"
    rows, spent, failures = generate(
        default_llm(), scenario_grid(args.scenarios, args.seed), budget_usd=args.budget_usd
    )
    print(f"cost ${spent:.4f}, failed calls {failures}")
    for split in ("train", "val"):
        chosen = [r for r in rows if split_of(r["scenario_seed"]) == split]
        (out / f"{split}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in chosen), "utf-8"
        )
        print(split, len(chosen))
