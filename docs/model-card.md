# Model card: dispute classifier

**Status: no encoder has been trained on real data, so there are no reportable classifier results.**

| | |
|---|---|
| **Task** | Dispute type (7 classes) and ordinal urgency (4 levels), from text only |
| **Serving default** | TF-IDF + logistic regression, trained on 71 assistant-written lines, until an exported encoder exists in `models/encoder-onnx` |
| **Candidates** | `answerdotai/ModernBERT-base`, `microsoft/deberta-v3-small`, zero-shot Claude, and a cascade |
| **Architecture** | A shared encoder with a type head and a cumulative-logit urgency head; temperature scaling; int8 ONNX |

## Intended use

Choosing which evidence playbook to run, in a demonstration. It's not a fraud detector: `suspected_fraud` only means the customer describes something suspicious. Fraud routing comes from records.

## Data

| Set | Status |
|---|---|
| `train.jsonl`, `val.jsonl` | Generate with `python -m adjudicate.classify.synth` (paid, Haiku). Split by scenario seed, so paraphrases can't straddle the split. |
| `test_human.jsonl` | **Must be written by a person before training.** `classifier_eval` refuses to report on anything else without `--smoke`. |
| `test_draft.jsonl` | 150 assistant-written lines from the draft golden set. Plumbing only. |

## To complete

```bash
python -m adjudicate.classify.synth                                # needs a key
# notebooks/train_encoder.ipynb on a hosted GPU, three seeds per encoder
python -m adjudicate.classify.export_onnx --model modernbert --temperature <from runs.jsonl>
python -m evals.classifier_eval --arms majority,tfidf,encoder,haiku,sonnet,opus,cascade
python -m evals.breakeven --vm-usd-per-month <price>
```

`evals/classifier_eval.py` reports macro-F1 and per-class F1, urgency QWK and critical recall, calibration error, latency on stated hardware, and cost per 1k disputes. The deliverable is a decision rule with a break-even volume, not a winner.

## Limits

- Latency on an i7-1355U laptop isn't server latency. Publish the hardware beside any figure.
- The `other` class has very few examples, and the baseline never predicts it correctly on the draft set.
- Results on one annotator's text may not hold for real complaints.
