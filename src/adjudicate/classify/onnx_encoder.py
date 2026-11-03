"""Serve the exported encoder on CPU with ONNX Runtime. Requires the `ml` extra."""

import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

from adjudicate.schemas import Classification


class OnnxEncoder:
    def __init__(self, model_dir: Path) -> None:
        meta = json.loads((model_dir / "meta.json").read_text("utf-8"))
        self.types, self.urgency = meta["types"], meta["urgency"]
        self.temperature = meta.get("temperature", 1.0)
        self.name = meta["model"]
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        path = model_dir / "model.int8.onnx"
        self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

    def predict(self, text: str) -> Classification:
        encoded = self.tokenizer([text], truncation=True, max_length=256, return_tensors="np")
        type_logits, urgency_logits = self.session.run(
            None,
            {
                "input_ids": encoded["input_ids"].astype(np.int64),
                "attention_mask": encoded["attention_mask"].astype(np.int64),
            },
        )
        scaled = type_logits[0] / self.temperature
        probs = np.exp(scaled - scaled.max())
        probs = probs / probs.sum()
        order = np.argsort(-probs)
        level = int((1 / (1 + np.exp(-urgency_logits[0])) > 0.5).sum())
        return Classification(
            dispute_type=self.types[order[0]],
            second_type=self.types[order[1]],
            urgency=self.urgency[level],
            type_probs={self.types[i]: round(float(probs[i]), 4) for i in order},
            confidence=round(float(probs[order[0]]), 4),
            model=self.name,
        )
