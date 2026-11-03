"""The cascade arm: use the local model, and ask the LLM only below a calibrated threshold."""

from dataclasses import dataclass

from adjudicate.classify.infer import Classifier
from adjudicate.classify.zeroshot import classify_llm
from adjudicate.llm import LLM, Usage
from adjudicate.schemas import Classification


@dataclass
class Cascade:
    local: Classifier
    llm: LLM
    model: str
    threshold: float

    def predict(self, text: str) -> tuple[Classification, Usage | None]:
        first = self.local.predict(text)
        if first.confidence >= self.threshold:
            return first, None
        return classify_llm(self.llm, self.model, text)
