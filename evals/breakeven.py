"""When is a self-hosted encoder cheaper than calling an LLM per dispute? Pure arithmetic."""

import argparse
import json


def break_even_per_day(
    vm_usd_per_month: float,
    llm_usd_per_1k: float,
    encoder_usd_per_1k: float = 0.0,
    retraining_usd_per_month: float = 0.0,
) -> float | None:
    """Disputes per day above which the encoder wins. None if the LLM is never dearer."""
    saving_per_dispute = (llm_usd_per_1k - encoder_usd_per_1k) / 1000
    if saving_per_dispute <= 0:
        return None
    return (vm_usd_per_month + retraining_usd_per_month) / saving_per_dispute / 30


def table(arms: dict[str, float], vm: float, retraining: float, encoder: float) -> list[dict]:
    return [
        {
            "llm": name,
            "llm_usd_per_1k": cost,
            "break_even_disputes_per_day": round(value, 1) if value is not None else None,
        }
        for name, cost in arms.items()
        for value in [break_even_per_day(vm, cost, encoder, retraining)]
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Feed in measured values from classifier_eval.")
    parser.add_argument("--vm-usd-per-month", type=float, required=True)
    parser.add_argument("--retraining-usd-per-month", type=float, default=0.0)
    parser.add_argument("--encoder-usd-per-1k", type=float, default=0.0)
    parser.add_argument("--classifier-json", default="evals/results/classifier.json")
    args = parser.parse_args()
    with open(args.classifier_json, encoding="utf-8") as handle:
        rows = json.load(handle)
    arms = {r["arm"]: r["usd_per_1k"] for r in rows if r["usd_per_1k"] > 0}
    print(
        json.dumps(
            table(
                arms, args.vm_usd_per_month, args.retraining_usd_per_month, args.encoder_usd_per_1k
            ),
            indent=1,
        )
    )
