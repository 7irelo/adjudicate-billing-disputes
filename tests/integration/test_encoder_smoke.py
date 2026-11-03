"""Train, export and serve an encoder, end to end, on a tiny random model. Proves the path, not F1.

Needs the `ml` extra and the network on first run. CI's free job has neither, so it skips there.
"""

import argparse
import json

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("onnxruntime")
pytest.importorskip("transformers")

TINY = "hf-internal-testing/tiny-random-BertModel"


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    from adjudicate.classify.train import train
    from adjudicate.config import REPO_ROOT

    out = tmp_path_factory.mktemp("encoder")
    seed = REPO_ROOT / "data/classifier/seed_train.jsonl"
    args = argparse.Namespace(
        model=TINY,
        train=str(seed),
        val=str(seed),
        out=str(out),
        epochs=1,
        batch_size=16,
        lr=1e-3,
        patience=1,
        seed=13,
    )
    try:
        return out, train(args)
    except OSError as exc:
        pytest.skip(f"couldn't download {TINY}: {exc}")


def test_training_logs_a_run_with_a_positive_temperature(trained) -> None:
    out, run = trained
    assert run["temperature"] > 0
    assert run["model"] == TINY
    logged = [json.loads(x) for x in (out / "runs.jsonl").read_text("utf-8").splitlines()]
    assert logged[-1]["seed"] == 13


def test_export_and_serve(trained, tmp_path) -> None:
    from adjudicate.classify.export_onnx import export
    from adjudicate.classify.onnx_encoder import OnnxEncoder

    out, run = trained
    export(TINY, out, tmp_path, run["temperature"])
    prediction = OnnxEncoder(tmp_path).predict("My monthly fee is on the invoice twice")
    assert prediction.dispute_type in prediction.type_probs
    assert 0 < prediction.confidence <= 1
    assert sum(prediction.type_probs.values()) == pytest.approx(1.0, abs=0.01)


def test_ordinal_targets_worked_by_hand() -> None:
    from adjudicate.classify.train import ordinal_predict, ordinal_targets

    targets = ordinal_targets(torch.tensor([0, 2, 3]), 4)
    assert targets.tolist() == [[0, 0, 0], [1, 1, 0], [1, 1, 1]]
    assert ordinal_predict(torch.tensor([[-9.0, -9.0, -9.0], [9.0, 9.0, -9.0]])).tolist() == [0, 2]


def test_temperature_scaling_cools_an_overconfident_model() -> None:
    from adjudicate.classify.train import fit_temperature

    # Very confident, and wrong half the time: the best temperature is well above 1.
    logits = torch.tensor([[10.0, 0.0], [10.0, 0.0], [0.0, 10.0], [0.0, 10.0]])
    labels = torch.tensor([0, 1, 1, 0])
    assert fit_temperature(logits, labels) > 2
