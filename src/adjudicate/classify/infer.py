"""Serve the best available classifier: an exported encoder if one exists, else the baseline."""

from functools import lru_cache
from typing import Protocol

from adjudicate.classify.baseline import BaselineClassifier, read_jsonl
from adjudicate.config import REPO_ROOT
from adjudicate.schemas import Classification

MODEL_DIR = REPO_ROOT / "models" / "encoder-onnx"
SEED_TRAIN = REPO_ROOT / "data" / "classifier" / "seed_train.jsonl"
TRAIN = REPO_ROOT / "data" / "classifier" / "train.jsonl"


class Classifier(Protocol):
    def predict(self, text: str) -> Classification: ...


@lru_cache
def load_classifier() -> Classifier:
    if MODEL_DIR.exists():
        from adjudicate.classify.onnx_encoder import OnnxEncoder

        return OnnxEncoder(MODEL_DIR)
    path = TRAIN if TRAIN.exists() else SEED_TRAIN
    return BaselineClassifier.train(read_jsonl(path))


def classify_text(text: str) -> Classification:
    return load_classifier().predict(text)
