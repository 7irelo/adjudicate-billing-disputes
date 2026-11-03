# pyright: reportArgumentType=false, reportCallIssue=false, reportAssignmentType=false
"""Compare classifier arms on the human-written test set, the only score that gets reported.

    python -m evals.classifier_eval --arms majority,tfidf
    python -m evals.classifier_eval --arms majority,tfidf,encoder,haiku,sonnet,opus,cascade   (paid)

It refuses to report on anything but data/classifier/test_human.jsonl unless told it's a smoke run,
because a score on generated text says little about real complaints.
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from sklearn.metrics import f1_score

from adjudicate.classify.baseline import BaselineClassifier, read_jsonl
from adjudicate.classify.cascade import Cascade
from adjudicate.classify.infer import MODEL_DIR, SEED_TRAIN, TRAIN
from adjudicate.classify.zeroshot import classify_llm
from adjudicate.config import REPO_ROOT, get_settings
from adjudicate.llm import AnthropicLLM
from evals.stats import bootstrap_ci, expected_calibration_error, quadratic_weighted_kappa

TEST = REPO_ROOT / "data" / "classifier" / "test_human.jsonl"
URGENCY = ["routine", "soon", "urgent", "critical"]
MODELS = {"haiku": "fast_model", "sonnet": "judge_model", "opus": "decide_model"}


def score(
    rows: list[dict], predictions: list[dict], latencies: list[float], costs: list[float]
) -> dict:
    true_types = [r["type"] for r in rows]
    predicted = [p["dispute_type"] for p in predictions]
    labels = sorted(set(true_types))
    per_class = f1_score(true_types, predicted, labels=labels, average=None, zero_division=0)
    # Macro-F1 isn't a mean over cases, so the CI resamples cases and recomputes it.
    correct = [t == p for t, p in zip(true_types, predicted, strict=True)]
    accuracy, low, high = bootstrap_ci(correct)
    true_urgency = [URGENCY.index(r["urgency"]) for r in rows]
    predicted_urgency = [URGENCY.index(p["urgency"]) for p in predictions]
    critical = [i for i, r in enumerate(rows) if r["urgency"] == "critical"]
    latencies = sorted(latencies)
    confident = [p["confidence"] for p in predictions]
    return {
        "n": len(rows),
        "type_macro_f1": round(
            float(f1_score(true_types, predicted, average="macro", zero_division=0)), 3
        ),
        "type_accuracy": {"mean": round(accuracy, 3), "ci": [round(low, 3), round(high, 3)]},
        "per_class_f1": {c: round(float(v), 3) for c, v in zip(labels, per_class, strict=True)},
        "urgency_macro_f1": round(
            float(f1_score(true_urgency, predicted_urgency, average="macro", zero_division=0)), 3
        ),
        "urgency_qwk": round(quadratic_weighted_kappa(true_urgency, predicted_urgency, 4), 3),
        "critical_recall": round(
            sum(predicted_urgency[i] == 3 for i in critical) / len(critical), 3
        )
        if critical
        else None,
        "ece": round(expected_calibration_error(confident, correct), 3),
        "p50_ms": round(latencies[len(latencies) // 2], 2),
        "p95_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 2),
        "usd_per_1k": round(1000 * sum(costs) / len(costs), 4),
    }


def run_arm(arm: str, rows: list[dict]) -> dict:
    train = read_jsonl(TRAIN if TRAIN.exists() else SEED_TRAIN)
    predictions, latencies, costs = [], [], []

    if arm == "majority":
        top_type = Counter(r["type"] for r in train).most_common(1)[0][0]
        top_urgency = Counter(r["urgency"] for r in train).most_common(1)[0][0]
        predict = lambda _t: (
            {"dispute_type": top_type, "urgency": top_urgency, "confidence": 1 / 7},
            0.0,
        )
    elif arm == "tfidf":
        model = BaselineClassifier.train(train)
        predict = lambda t: (model.predict(t).model_dump(), 0.0)
    elif arm == "encoder":
        from adjudicate.classify.onnx_encoder import OnnxEncoder

        encoder = OnnxEncoder(MODEL_DIR)
        predict = lambda t: (encoder.predict(t).model_dump(), 0.0)
    elif arm in MODELS:
        model_id = getattr(get_settings(), MODELS[arm])
        llm = AnthropicLLM()

        def predict(t: str):
            got, usage = classify_llm(llm, model_id, t)
            return got.model_dump(), usage.cost_usd
    elif arm == "cascade":
        local = BaselineClassifier.train(train)
        if MODEL_DIR.exists():
            from adjudicate.classify.onnx_encoder import OnnxEncoder

            local = OnnxEncoder(MODEL_DIR)
        cascade = Cascade(local, AnthropicLLM(), get_settings().fast_model, threshold=0.7)  # pyright: ignore[reportArgumentType]

        def predict(t: str):
            got, usage = cascade.predict(t)
            return got.model_dump(), usage.cost_usd if usage else 0.0
    else:
        raise ValueError(arm)

    for row in rows:
        began = time.perf_counter()
        prediction, cost = predict(row["text"])
        latencies.append((time.perf_counter() - began) * 1000)
        predictions.append(prediction)
        costs.append(cost)
    return {"arm": arm, **score(rows, predictions, latencies, costs)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default="majority,tfidf")
    parser.add_argument("--test", type=Path, default=TEST)
    parser.add_argument(
        "--smoke", action="store_true", help="allow a non-human test file; not reportable"
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals/results/classifier.json")
    args = parser.parse_args()
    if args.test != TEST and not args.smoke:
        parser.error("only test_human.jsonl is reportable; pass --smoke for a plumbing check")
    if not args.test.exists() or not read_jsonl(args.test):
        raise SystemExit(
            f"{args.test} has no rows yet. A person must write it before any training."
        )
    rows = read_jsonl(args.test)
    results = [run_arm(arm, rows) for arm in args.arms.split(",")]
    for result in results:
        print(json.dumps(result))
    args.out.write_text(json.dumps(results, indent=1), "utf-8")


if __name__ == "__main__":
    main()
