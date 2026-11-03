# 5. A deterministic fraud screen that overrides the model

**Decision.** Explainable signals computed from records. A flag skips the model and the calculator and goes to the fraud queue. Dispute text isn't an input, so saying "fraud" can't trigger the screen, and a persuasive rationale can't get past it.

**Rejected.** LLM judgement isn't auditable or non-negotiable. A trained anomaly model on synthetic labels would only learn the generator.

**Known limit.** The low-and-slow pattern is built to stay under the thresholds, and a test records that it does. Such a case can reach the model. Its protection there is the customer's denial leading to `needs_review`, plus the hard rule that any fraud-scenario case reaching auto-credit fails the build.

**Thresholds** are chosen by `evals/fraud_eval.py` on one seed and reported on another, because a screen that catches everything has been tuned to its own generator. These are synthetic patterns, so the numbers say nothing about real fraud.
