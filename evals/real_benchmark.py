"""The chunking comparison on real documents the author didn't write.

Authored T&Cs are short and cleanly structured, so clause-aware chunking might only "win" there.
This parses real PDFs, builds every strategy in memory, and scores on span labels. Nothing here is
loaded into Postgres or served to a tenant.

    python -m adjudicate.ingest.fetch
    python -m evals.real_benchmark
"""

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from adjudicate.config import REPO_ROOT
from adjudicate.ingest.chunkers import STRATEGIES, chunk_document
from adjudicate.ingest.parse import parse_check, parse_pdf
from adjudicate.retrieval.pipeline import Bm25Index
from evals.retrieval_eval import gold_ranks, score_query
from evals.stats import bootstrap_ci

RAW = REPO_ROOT / "corpus" / "raw"
QRELS = REPO_ROOT / "data" / "retrieval" / "qrels_real.jsonl"
MANIFEST = REPO_ROOT / "corpus" / "manifest.yaml"
CANDIDATES = 50


def load_documents() -> list:
    manifest = yaml.safe_load(MANIFEST.read_text("utf-8"))
    docs = []
    for entry in manifest["documents"]:
        path = RAW / f"{entry['id']}.pdf"
        if path.exists():
            docs.append(parse_pdf(path, entry["id"], entry["title"]))
    return docs


def gold_spans(doc, qrels: list[dict]) -> dict[str, tuple[str, int, int]]:
    """Gold is a verbatim snippet located in the parsed text, so it doesn't depend on chunking."""
    spans = {}
    for q in qrels:
        if q["document"] != doc.document_id:
            continue
        for index, snippet in enumerate(q["gold_snippets"]):
            start = doc.full_text.find(snippet)
            if start < 0:
                raise ValueError(f"{q['qid']}: snippet not in parsed text: {snippet[:60]!r}")
            spans[f"{q['qid']}#{index}"] = (doc.document_id, start, start + len(snippet))
    return spans


def dense_rank(embedder, chunks: list, query: str, cache: dict) -> list[str]:
    """Exact cosine in memory: nothing here goes near a tenant's database."""
    key = chunks[0].strategy if chunks else ""
    if key not in cache:
        cache[key] = np.array(embedder.encode_many([c.match_text for c in chunks]))
    scores = cache[key] @ np.array(embedder.encode_query(query))
    return [chunks[int(i)].chunk_id for i in np.argsort(-scores)[:CANDIDATES]]


def run(doc, qrels: list[dict], embedder=None) -> list[dict[str, Any]]:
    spans = gold_spans(doc, qrels)
    relevant = [q for q in qrels if q["document"] == doc.document_id]
    rows = []
    vectors: dict = {}
    for strategy in STRATEGIES:
        chunks = chunk_document(doc, strategy)
        index = Bm25Index([c.chunk_id for c in chunks], [c.match_text for c in chunks])
        clause_spans = {f"{c.clause_id}#E": (c.char_start, c.char_end) for c in doc.clauses}
        by_id = {
            c.chunk_id: {
                # E returns a whole section but matched one clause. Scoring on the section would
                # credit it with finding every sibling, so gold is checked against the clause.
                "chunk_id": c.chunk_id.replace("#E", "#e"),
                "document_id": c.document_id,
                "char_start": clause_spans[c.chunk_id][0] if strategy == "E" else c.char_start,
                "char_end": clause_spans[c.chunk_id][1] if strategy == "E" else c.char_end,
                "text": c.text,
            }
            for c in chunks
        }
        per_query = []
        for q in relevant:
            gold = [f"{q['qid']}#{i}" for i in range(len(q["gold_snippets"]))]
            if embedder is None:
                ids = [h.chunk_id for h in index.search(q["query"], CANDIDATES)]
            else:
                ids = dense_rank(embedder, chunks, q["query"], vectors)
            hits = [by_id[i] for i in ids]
            scores = score_query(gold_ranks(hits, gold, spans), gold)
            scores["context_tokens@5"] = sum(len(h["text"].split()) for h in hits[:5])
            per_query.append(scores)
        summary: dict[str, Any] = {
            "document": doc.document_id,
            "strategy": strategy,
            "mode": "dense" if embedder else "bm25",
            "chunks": len(chunks),
            "n": len(per_query),
        }
        for metric in per_query[0]:
            mean, low, high = bootstrap_ci([p[metric] for p in per_query])
            summary[metric] = {"mean": round(mean, 3), "ci": [round(low, 3), round(high, 3)]}
        rows.append(summary)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "evals/results/summaries/real_benchmark.json"
    )
    parser.add_argument("--dense", action="store_true", help="BGE-M3, needs the ml extra")
    args = parser.parse_args()
    qrels = [json.loads(x) for x in QRELS.read_text("utf-8").splitlines() if x.strip()]
    documents = load_documents()
    if not documents:
        raise SystemExit("No PDFs in corpus/raw. Run: python -m adjudicate.ingest.fetch")
    checks = [parse_check(d) for d in documents]
    embedder = None
    if args.dense:
        from adjudicate.retrieval.models import BgeEmbedder

        embedder = BgeEmbedder()
    results = []
    for doc in documents:
        if any(q["document"] == doc.document_id for q in qrels):
            results.extend(run(doc, qrels, embedder))
    print(
        f"{'doc/strategy':<30}{'chunks':>7}{'R@1':>7}{'R@5':>7}{'MRR':>7}{'ctx@5':>7}  R@5 95% CI"
    )
    for r in results:
        name = f"{r['document'][:24]}/{r['strategy']}"
        print(
            f"{name:<30}{r['chunks']:>7}{r['recall@1']['mean']:>7}{r['recall@5']['mean']:>7}"
            f"{r['mrr']['mean']:>7}{round(r['context_tokens@5']['mean']):>7}  {r['recall@5']['ci']}"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "note": "BM25 on real documents. Labels are assistant drafts, not reportable.",
                "parse_checks": [{k: v for k, v in c.items() if k != "sample"} for c in checks],
                "results": results,
            },
            indent=1,
        )
        + "\n",
        "utf-8",
    )
    for check in checks:
        print(json.dumps({k: v for k, v in check.items() if k != "sample"}))


if __name__ == "__main__":
    main()
