"""Export a trained two-head encoder to ONNX, with int8 quantisation. Requires the `ml` extra."""

import argparse
import json
from pathlib import Path

import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import AutoTokenizer

from adjudicate.classify.train import MODELS, TYPES, URGENCY, TwoHeadEncoder


def export(model_name: str, checkpoint: Path, out: Path, temperature: float) -> Path:
    name = MODELS.get(model_name, model_name)
    model = TwoHeadEncoder(name, len(TYPES), len(URGENCY))
    model.load_state_dict(torch.load(checkpoint / "best.pt", map_location="cpu"))
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(checkpoint)
    sample = tokenizer(["sample dispute text"], return_tensors="pt")
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "model.onnx"
    torch.onnx.export(
        model,
        (sample["input_ids"], sample["attention_mask"]),
        raw,
        input_names=["input_ids", "attention_mask"],
        output_names=["type_logits", "urgency_logits"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "tokens"},
            "attention_mask": {0: "batch", 1: "tokens"},
        },
        opset_version=17,
        dynamo=False,  # the new exporter produces graphs that int8 quantisation rejects
    )
    quantised = out / "model.int8.onnx"
    quantize_dynamic(raw, quantised, weight_type=QuantType.QInt8)
    tokenizer.save_pretrained(out)
    (out / "meta.json").write_text(
        json.dumps(
            {"model": name, "temperature": temperature, "types": TYPES, "urgency": URGENCY},
            indent=1,
        ),
        "utf-8",
    )
    return quantised


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="modernbert")
    parser.add_argument("--checkpoint", type=Path, default=Path("models/encoder"))
    parser.add_argument("--out", type=Path, default=Path("models/encoder-onnx"))
    parser.add_argument(
        "--temperature", type=float, default=1.0, help="from temperature scaling on val"
    )
    args = parser.parse_args()
    print(export(args.model, args.checkpoint, args.out, args.temperature))
