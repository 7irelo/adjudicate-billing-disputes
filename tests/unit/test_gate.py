"""Each gate rule, tested directly."""

from evals.gate import evaluate, make_baseline
from evals.stats import bootstrap_ci, cohens_kappa, paired_bootstrap_diff, quadratic_weighted_kappa


def row(case_id: str, correct: float) -> dict:
    return {"case_id": case_id, "grade": {"decision_correct": correct}}


def summary(mean: float = 1.0, **extra) -> dict:
    return {"metrics": {"decision_correct": {"mean": mean}}, "infra_error_rate": 0.0, **extra}


def test_outage_is_errored_not_failed() -> None:
    code, _ = evaluate(summary(infra_error_rate=0.2), [row("a", 1)], {})
    assert code == 2


def test_budget_stop_is_errored() -> None:
    code, _ = evaluate(summary(meta={"budget_stopped": True}), [row("a", 1)], {})
    assert code == 2


def test_zero_tolerance_fails_even_with_good_metrics() -> None:
    bad = summary(zero_tolerance=[{"case_id": "G-1", "problems": ["fraud case auto-credited"]}])
    code, reasons = evaluate(bad, [row("a", 1)], {})
    assert code == 1 and "fraud" in reasons[0]


def test_floor() -> None:
    code, _ = evaluate(summary(0.5), [row("a", 0.5)], {"floors": {"decision_correct": 0.7}})
    assert code == 1


def test_large_regression_fails_and_noise_passes() -> None:
    ids = [f"G-{i}" for i in range(30)]
    good = [row(i, 1.0) for i in ids]
    baseline = make_baseline(good, "test", ["decision_correct"], margin=0.05)
    baseline["floors"] = {}
    # All wrong: the whole CI is far below the allowed drop.
    code, _ = evaluate(summary(0.0), [row(i, 0.0) for i in ids], baseline)
    assert code == 1
    # One wrong in 30: within the noise, so it doesn't fail.
    noisy = [row(i, 0.0 if n == 0 else 1.0) for n, i in enumerate(ids)]
    code, _ = evaluate(summary(0.97), noisy, baseline)
    assert code == 0


def test_bootstrap_is_reproducible_and_brackets_the_mean() -> None:
    values = [1, 0, 1, 1, 0, 1, 1, 1, 0, 1]
    assert bootstrap_ci(values) == bootstrap_ci(values)
    mean, low, high = bootstrap_ci(values)
    assert low <= mean <= high


def test_paired_diff_of_identical_samples_is_zero() -> None:
    assert paired_bootstrap_diff([1, 0, 1], [1, 0, 1]) == (0.0, 0.0, 0.0)


def test_kappa_known_values() -> None:
    assert cohens_kappa(["a", "b"], ["a", "b"]) == 1.0
    # Worked by hand: observed 0.5, expected 0.5, so kappa 0.
    assert cohens_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    assert quadratic_weighted_kappa([0, 1, 2], [0, 1, 2], 3) == 1.0
