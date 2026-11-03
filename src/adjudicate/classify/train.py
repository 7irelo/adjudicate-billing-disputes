"""Fine-tune an encoder with two heads: dispute type and ordinal urgency. Needs a GPU in practice,
so notebooks/train_encoder.ipynb runs this on Kaggle or Colab. Requires the `ml` extra."""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import f1_score
from torch import nn
from transformers import AutoConfig, AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

from adjudicate.classify.baseline import read_jsonl

TYPES = [
    "roaming",
    "international",
    "cancellation",
    "double_billing",
    "out_of_bundle",
    "suspected_fraud",
    "other",
]
URGENCY = ["routine", "soon", "urgent", "critical"]
MODELS = {
    "modernbert": "answerdotai/ModernBERT-base",
    "deberta": "microsoft/deberta-v3-small",
}


class TwoHeadEncoder(nn.Module):
    """Urgency uses cumulative logits (CORAL style), so errors far from the truth cost more."""

    def __init__(self, name: str, num_types: int, num_urgency: int) -> None:
        super().__init__()
        self.config = AutoConfig.from_pretrained(name)
        self.encoder = AutoModel.from_pretrained(name)
        self.type_head = nn.Linear(self.config.hidden_size, num_types)
        self.urgency_head = nn.Linear(self.config.hidden_size, num_urgency - 1)

    def forward(self, input_ids, attention_mask):
        hidden = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        mask = attention_mask.unsqueeze(-1).float()
        pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
        return self.type_head(pooled), self.urgency_head(pooled)


def ordinal_targets(urgency: torch.Tensor, levels: int) -> torch.Tensor:
    thresholds = torch.arange(levels - 1, device=urgency.device).unsqueeze(0)
    return (urgency.unsqueeze(1) > thresholds).float()


def ordinal_predict(logits: torch.Tensor) -> torch.Tensor:
    return (torch.sigmoid(logits) > 0.5).sum(dim=1)


def _batches(rows, tokenizer, batch_size, device, shuffle, seed=0):
    order = np.random.default_rng(seed).permutation(len(rows)) if shuffle else np.arange(len(rows))
    for i in range(0, len(rows), batch_size):
        chunk = [rows[j] for j in order[i : i + batch_size]]
        encoded = tokenizer(
            [r["text"] for r in chunk],
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="pt",
        ).to(device)
        types = torch.tensor([TYPES.index(r["type"]) for r in chunk], device=device)
        urgency = torch.tensor([URGENCY.index(r["urgency"]) for r in chunk], device=device)
        yield encoded, types, urgency


def evaluate(model, rows, tokenizer, device) -> dict:
    model.eval()
    all_types, all_pred = [], []
    urgency_true, urgency_pred = [], []
    with torch.no_grad():
        for encoded, types, urgency in _batches(rows, tokenizer, 64, device, shuffle=False):
            type_logits, urgency_logits = model(encoded["input_ids"], encoded["attention_mask"])
            all_types += types.tolist()
            all_pred += type_logits.argmax(1).tolist()
            urgency_true += urgency.tolist()
            urgency_pred += ordinal_predict(urgency_logits).tolist()
    return {
        "type_macro_f1": float(f1_score(all_types, all_pred, average="macro")),
        "urgency_macro_f1": float(f1_score(urgency_true, urgency_pred, average="macro")),
    }


def fit_temperature(logits: torch.Tensor, labels: torch.Tensor, steps: int = 200) -> float:
    """Temperature scaling on validation logits: one parameter, so it can't change the ranking."""
    log_temperature = torch.zeros(1, requires_grad=True)
    optimiser = torch.optim.LBFGS([log_temperature], lr=0.1, max_iter=steps)
    loss_fn = nn.CrossEntropyLoss()

    def closure():
        optimiser.zero_grad()
        loss = loss_fn(logits / log_temperature.exp(), labels)
        loss.backward()
        return loss

    optimiser.step(closure)
    return float(log_temperature.exp().item())


def validation_logits(model, rows, tokenizer, device) -> tuple[torch.Tensor, torch.Tensor]:
    model.eval()
    all_logits, all_labels = [], []
    with torch.no_grad():
        for encoded, types, _ in _batches(rows, tokenizer, 64, device, shuffle=False):
            logits, _ = model(encoded["input_ids"], encoded["attention_mask"])
            all_logits.append(logits.cpu())
            all_labels.append(types.cpu())
    return torch.cat(all_logits), torch.cat(all_labels)


def train(args: argparse.Namespace) -> dict:
    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    name = MODELS.get(args.model, args.model)
    tokenizer = AutoTokenizer.from_pretrained(name)
    train_rows, val_rows = read_jsonl(Path(args.train)), read_jsonl(Path(args.val))

    model = TwoHeadEncoder(str(name), len(TYPES), len(URGENCY)).to(device)
    counts = np.bincount([TYPES.index(r["type"]) for r in train_rows], minlength=len(TYPES))
    weights = torch.tensor(
        counts.sum() / (len(TYPES) * np.maximum(counts, 1)), device=device
    ).float()
    type_loss = nn.CrossEntropyLoss(weight=weights)
    urgency_loss = nn.BCEWithLogitsLoss()
    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    total = args.epochs * (len(train_rows) // args.batch_size + 1)
    schedule = get_linear_schedule_with_warmup(optimiser, total // 10, total)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    best, patience, log = -1.0, 0, []
    for epoch in range(args.epochs):
        model.train()
        for encoded, types, urgency in _batches(
            train_rows, tokenizer, args.batch_size, device, shuffle=True, seed=args.seed + epoch
        ):
            type_logits, urgency_logits = model(encoded["input_ids"], encoded["attention_mask"])
            loss = type_loss(type_logits, types) + urgency_loss(
                urgency_logits, ordinal_targets(urgency, len(URGENCY))
            )
            optimiser.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            schedule.step()
        metrics = {"epoch": epoch, **evaluate(model, val_rows, tokenizer, device)}
        log.append(metrics)
        print(json.dumps(metrics))
        if metrics["type_macro_f1"] > best:
            best, patience = metrics["type_macro_f1"], 0
            torch.save(model.state_dict(), out / "best.pt")
            tokenizer.save_pretrained(out)
        else:
            patience += 1
            if patience >= args.patience:
                break
    model.load_state_dict(torch.load(out / "best.pt", map_location=device))
    temperature = fit_temperature(*validation_logits(model, val_rows, tokenizer, device))
    run = {
        "model": name,
        "seed": args.seed,
        "best_val_type_macro_f1": best,
        "temperature": temperature,
        "log": log,
    }
    with (out / "runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run) + "\n")
    return run


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="modernbert")
    parser.add_argument("--train", default="data/classifier/train.jsonl")
    parser.add_argument("--val", default="data/classifier/val.jsonl")
    parser.add_argument("--out", default="models/encoder")
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=3e-5)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--seed", type=int, default=13)
    print(train(parser.parse_args()))
