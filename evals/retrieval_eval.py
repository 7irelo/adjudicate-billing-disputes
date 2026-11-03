"""The chunking experiment: 5 strategies x 4 retrieval modes, scored by span overlap.

Chunk IDs differ between strategies, so gold is expressed as character spans. Hits are deduplicated
to distinct gold clauses before ranks are computed, which is what makes strategies comparable.

    python -m evals.retrieval_eval --split dev --modes bm25
    python -m evals.retrieval_eval --split dev --modes bm25,dense,hybrid,rerank    (needs the ml extra)
"""

import argparse
import json
import math
import time
from datetime import date
from pathlib import Path
from typing import Any

from adjudicate.config import REPO_ROOT
from adjudicate.db import TenantPool
from adjudicate.ingest.pipeline import parse_corpus
from adjudicate.retrieval.expansion import rewrite_query
from adjudicate.retrieval.pipeline import (
    SearchConfig,
    clear_cache,
    dense_search,
    hydrate_chunks,
    rrf,
    tenant_bm25,
)
from evals.stats import bootstrap_ci, paired_bootstrap_diff

QRELS = REPO_ROOT / "data" / "retrieval" / "qrels.jsonl"
MIN_OVERLAP = 0.5  # of the gold clause's span
KS = (1, 3, 5, 10)


def load_qrels(split: str | None = None) -> list[dict]:
    rows = [json.loads(x) for x in QRELS.read_text("utf-8").splitlines() if x.strip()]
    return [r for r in rows if split is None or r["split"] == split]


def clause_spans() -> dict[str, tuple[str, int, int]]:
    spans = {}
    for doc in parse_corpus():
        for c in doc.clauses:
            spans[c.clause_id] = (doc.document_id, c.char_start, c.char_end)
    return spans


def overlaps(chunk: dict, span: tuple[str, int, int]) -> bool:
    document_id, start, end = span
    if chunk["document_id"] != document_id:
        return False
    shared = min(chunk["char_end"], end) - max(chunk["char_start"], start)
    return shared >= MIN_OVERLAP * (end - start)


def gold_ranks(chunks: list[dict], gold: list[str], spans: dict) -> dict[str, int]:
    """The rank of each gold clause, counting each distinct item once."""
    ranks: dict[str, int] = {}
    rank = 0
    seen: set[str] = set()
    for chunk in chunks:
        # E's chunks return a whole section but match on one clause, so gold is matched on the
        # matched clause for E, otherwise a section would count as finding all its clauses.
        if chunk["chunk_id"].endswith("#E"):
            direct = chunk["chunk_id"].split("#")[0]
            matched = [g for g in gold if g == direct]
        else:
            matched = [g for g in gold if overlaps(chunk, spans[g])]
        if chunk["chunk_id"] in seen:
            continue
        seen.add(chunk["chunk_id"])
        rank += 1
        for g in matched:
            ranks.setdefault(g, rank)
    return ranks


def score_query(ranks: dict[str, int], gold: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for k in KS:
        scores[f"recall@{k}"] = sum(1 for g in gold if ranks.get(g, 10**6) <= k) / len(gold)
    best = min(ranks.values(), default=None)
    scores["mrr"] = 1 / best if best else 0.0
    # Several gold clauses in one chunk can't all sit at that rank: the reader still has to find
    # them one after another. Spreading ties keeps nDCG at or below 1.
    found = sorted(rank for g, rank in ranks.items() if g in gold and rank <= 10)
    dcg = sum(1 / math.log2(max(rank, i + 1) + 1) for i, rank in enumerate(found))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    scores["ndcg@10"] = dcg / ideal if ideal else 0.0
    return scores


def _query(query: str, cfg: SearchConfig) -> str:
    return rewrite_query(query) if cfg.rewrite else query


def retrieve(
    conn,
    tenant: str,
    query: str,
    as_of: date | None,
    cfg: SearchConfig,
    embedder: Any = None,
    reranker: Any = None,
) -> list[dict]:
    query = _query(query, cfg)
    rankings = []
    if cfg.mode in {"bm25", "hybrid", "rerank"}:
        rankings.append(tenant_bm25(conn, tenant, cfg.strategy).search(query, cfg.candidates))
    if cfg.mode in {"dense", "hybrid", "rerank"}:
        rankings.append(
            dense_search(conn, embedder.encode_query(query), cfg.strategy, cfg.candidates)
        )
    hits = rankings[0] if len(rankings) == 1 else rrf(rankings)
    rows = hydrate_chunks(conn, hits, as_of)
    if cfg.mode == "rerank":
        head = rows[: cfg.rerank_candidates]
        scores = reranker.score(query, [r["match_text"] for r in head])
        rows = [
            row
            for row, _ in sorted(
                zip(head, scores, strict=True), key=lambda pair: (-pair[1], pair[0]["chunk_id"])
            )
        ]
    return rows


def run_config(
    pool: TenantPool,
    qrels: list[dict],
    cfg: SearchConfig,
    spans: dict,
    embedder: Any = None,
    reranker: Any = None,
) -> dict[str, Any]:
    per_query = []
    latencies = []
    for q in qrels:
        as_of = date.fromisoformat(q["as_of"]) if q.get("as_of") else None
        began = time.perf_counter()
        with pool.tenant_conn(q["tenant"]) as conn:
            rows = retrieve(conn, q["tenant"], q["query"], as_of, cfg, embedder, reranker)
        latencies.append((time.perf_counter() - began) * 1000)
        scores = score_query(gold_ranks(rows, q["gold_clauses"], spans), q["gold_clauses"])
        # What retrieval costs the model. A chunk holding the whole document gets perfect recall
        # for free, so recall can't be read without this.
        scores["context_tokens@5"] = sum(len(r["text"].split()) for r in rows[:5])
        per_query.append(scores)
    latencies.sort()
    summary: dict[str, Any] = {
        "strategy": cfg.strategy,
        "mode": cfg.mode,
        "n": len(per_query),
        "p50_ms": round(latencies[len(latencies) // 2], 1),
        "p95_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 1),
    }
    for metric in per_query[0]:
        mean, low, high = bootstrap_ci([p[metric] for p in per_query])
        summary[metric] = {"mean": round(mean, 3), "ci": [round(low, 3), round(high, 3)]}
    summary["per_query"] = per_query
    return summary


def compare(a: dict, b: dict, metric: str = "recall@5") -> dict[str, float]:
    diff, low, high = paired_bootstrap_diff(
        [q[metric] for q in a["per_query"]], [q[metric] for q in b["per_query"]]
    )
    return {"diff": round(diff, 3), "low": round(low, 3), "high": round(high, 3)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="dev")
    parser.add_argument("--strategies", default="A,B,C,D,E")
    parser.add_argument("--modes", default="bm25")
    parser.add_argument("--rewrite", action="store_true", help="query rewriting on")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals/results/retrieval.json")
    args = parser.parse_args()
    embedder = reranker = None
    modes = args.modes.split(",")
    if set(modes) & {"dense", "hybrid", "rerank"}:
        from adjudicate.retrieval.models import BgeEmbedder, BgeReranker

        embedder, reranker = BgeEmbedder(), BgeReranker()
    qrels = load_qrels(args.split)
    spans = clause_spans()
    pool = TenantPool()
    clear_cache()
    results = [
        run_config(
            pool,
            qrels,
            SearchConfig(strategy=s, mode=m, rewrite=args.rewrite),
            spans,
            embedder,
            reranker,
        )
        for s in args.strategies.split(",")
        for m in modes
    ]
    print(
        f"{'config':<12}{'R@1':>7}{'R@3':>7}{'R@5':>7}{'R@10':>7}{'MRR':>7}{'nDCG':>7}{'ctx@5':>7}{'p50ms':>8}   R@5 95% CI"
    )
    for r in results:
        name = f"{r['strategy']}/{r['mode']}"
        print(
            f"{name:<12}{r['recall@1']['mean']:>7}{r['recall@3']['mean']:>7}{r['recall@5']['mean']:>7}"
            f"{r['recall@10']['mean']:>7}{r['mrr']['mean']:>7}{r['ndcg@10']['mean']:>7}{round(r['context_tokens@5']['mean']):>7}{r['p50_ms']:>8}"
            f"   {r['recall@5']['ci']}"
        )
    if len(results) > 1:
        reference = results[0]
        for other in results[1:]:
            pair = compare(reference, other)
            print(
                f"{other['strategy']}/{other['mode']} minus {reference['strategy']}/{reference['mode']}: {pair}"
            )
    args.out.write_text(json.dumps(results, indent=1), "utf-8")


if __name__ == "__main__":
    main()
