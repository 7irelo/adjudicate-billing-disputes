"""TF-IDF + logistic regression: the floor every other arm has to beat, and the default until an
encoder has been trained. Trains in milliseconds on whatever labelled file it's given."""

import json
from dataclasses import dataclass
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline

from adjudicate.schemas import Classification


def read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _pipeline() -> Pipeline:
    return make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1),
        LogisticRegression(max_iter=2000, class_weight="balanced", C=10.0),
    )


@dataclass
class BaselineClassifier:
    type_model: Pipeline
    urgency_model: Pipeline
    name: str = "tfidf-logreg"

    @classmethod
    def train(cls, rows: list[dict]) -> "BaselineClassifier":
        texts = [r["text"] for r in rows]
        type_model = _pipeline().fit(texts, [r["type"] for r in rows])
        urgency_model = _pipeline().fit(texts, [r["urgency"] for r in rows])
        return cls(type_model, urgency_model)

    def predict(self, text: str) -> Classification:
        probs = self.type_model.predict_proba([text])[0]
        classes = list(self.type_model.classes_)
        ranked = sorted(zip(classes, probs, strict=True), key=lambda pair: -pair[1])
        urgency = str(self.urgency_model.predict([text])[0])
        return Classification(
            dispute_type=ranked[0][0],
            second_type=ranked[1][0],
            urgency=urgency,  # pyright: ignore[reportArgumentType]
            type_probs={str(c): round(float(p), 4) for c, p in ranked},
            confidence=round(float(ranked[0][1]), 4),
            model=self.name,
        )
