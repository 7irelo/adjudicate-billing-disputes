"""Clause search: BM25, dense (pgvector), RRF fusion, reranking, scope and date filters.

BM25 indexes are per tenant, built only from what RLS shows that tenant. The in-process cache is
keyed on tenant. Even with a wrong index, hydration through RLS returns nothing for foreign IDs.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import bm25s

from adjudicate.db import DictConnection
from adjudicate.schemas import Clause, RetrievedClause

RRF_K = 60


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: str
    score: float


class Bm25Index:
    def __init__(self, chunk_ids: list[str], texts: list[str]) -> None:
        self.chunk_ids = chunk_ids
        self.retriever = bm25s.BM25()
        self.retriever.index(
            bm25s.tokenize(texts, stopwords="en", show_progress=False), show_progress=False
        )

    def search(self, query: str, k: int) -> list[ChunkHit]:
        tokens = bm25s.tokenize([query], stopwords="en", show_progress=False)
        limit = min(k, len(self.chunk_ids))
        indices, scores = self.retriever.retrieve(tokens, k=limit, show_progress=False)
        return [
            ChunkHit(self.chunk_ids[int(i)], float(s))
            for i, s in zip(indices[0], scores[0], strict=True)
            if float(s) > 0
        ]


class Embedder(Protocol):
    def encode_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


def rrf(rankings: Sequence[Sequence[ChunkHit]], k: int = RRF_K) -> list[ChunkHit]:
    """Reciprocal rank fusion. Ties break on chunk ID so runs are reproducible."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
    return [ChunkHit(c, s) for c, s in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


_bm25_cache: dict[tuple[str, str], Bm25Index] = {}


def build_bm25(conn: DictConnection, strategy: str) -> Bm25Index:
    rows = conn.execute(
        "SELECT chunk_id, match_text FROM chunks WHERE strategy = %s ORDER BY chunk_id",
        (strategy,),
    ).fetchall()
    if not rows:
        raise LookupError(f"no chunks for strategy {strategy}")
    return Bm25Index([r["chunk_id"] for r in rows], [r["match_text"] for r in rows])


def tenant_bm25(conn: DictConnection, tenant_id: str, strategy: str) -> Bm25Index:
    key = (tenant_id, strategy)
    if key not in _bm25_cache:
        _bm25_cache[key] = build_bm25(conn, strategy)
    return _bm25_cache[key]


def clear_cache() -> None:
    _bm25_cache.clear()


def dense_search(
    conn: DictConnection, embedding: list[float], strategy: str, k: int
) -> list[ChunkHit]:
    """Exact scan under RLS. The corpus is small, so no ANN index is needed."""
    vector = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
    rows = conn.execute(
        "SELECT chunk_id, 1 - (embedding <=> %s::vector) AS score FROM chunks "
        "WHERE strategy = %s AND embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT %s",
        (vector, strategy, vector, k),
    ).fetchall()
    return [ChunkHit(r["chunk_id"], float(r["score"])) for r in rows]


def hydrate_chunks(
    conn: DictConnection, hits: Sequence[ChunkHit], as_of: date | None
) -> list[dict]:
    """Fetch hits through RLS, in rank order, dropping foreign IDs and out-of-date documents."""
    if not hits:
        return []
    rows = conn.execute(
        "SELECT chunk_id, document_id, tenant_id, text, match_text, char_start, char_end, "
        "effective_from, effective_to FROM chunks WHERE chunk_id = ANY(%s)",
        ([h.chunk_id for h in hits],),
    ).fetchall()
    by_id = {r["chunk_id"]: r for r in rows}
    out = []
    for hit in hits:
        row = by_id.get(hit.chunk_id)
        if row is None:
            continue
        if as_of and not (
            row["effective_from"] <= as_of
            and (row["effective_to"] is None or row["effective_to"] >= as_of)
        ):
            continue
        out.append({**row, "score": hit.score})
    return out


def row_to_clause(row: dict[str, Any]) -> Clause:
    return Clause(
        clause_id=row["clause_id"],
        scope="tenant" if row["tenant_id"] else "global",
        kind=row["kind"],
        path=row["path"],
        text=row["text"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        char_start=row["char_start"],
        char_end=row["char_end"],
    )


def get_clause(conn: DictConnection, clause_id: str) -> Clause | None:
    row = conn.execute("SELECT * FROM clauses WHERE clause_id = %s", (clause_id,)).fetchone()
    return row_to_clause(row) if row else None


def clauses_for_chunks(conn: DictConnection, chunk_rows: list[dict]) -> list[tuple[Clause, float]]:
    """Map chunks to distinct clauses by span overlap, keeping rank order and the best score."""
    if not chunk_rows:
        return []
    docs = list({r["document_id"] for r in chunk_rows})
    all_clauses = conn.execute(
        "SELECT * FROM clauses WHERE document_id = ANY(%s) ORDER BY char_start", (docs,)
    ).fetchall()
    seen: set[str] = set()
    out: list[tuple[Clause, float]] = []
    for chunk in chunk_rows:
        # Strategies C, D and E carry the matched clause in the chunk ID.
        direct = chunk["chunk_id"].split("#")[0] if ":" in chunk["chunk_id"] else None
        for row in all_clauses:
            same_doc = row["document_id"] == chunk["document_id"]
            overlaps = (
                row["char_start"] < chunk["char_end"] and chunk["char_start"] < row["char_end"]
            )
            matched = row["clause_id"] == direct if direct else (same_doc and overlaps)
            if matched and row["clause_id"] not in seen:
                seen.add(row["clause_id"])
                out.append((row_to_clause(row), chunk["score"]))
    return out


@dataclass
class SearchConfig:
    strategy: str = "D"
    mode: str = "bm25"  # bm25 | dense | hybrid | rerank
    candidates: int = 50
    rerank_candidates: int = 20


def search_clauses(
    conn: DictConnection,
    tenant_id: str,
    query: str,
    k: int = 8,
    as_of: date | None = None,
    config: SearchConfig | None = None,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
    index_factory: Callable[[DictConnection, str, str], Bm25Index] = tenant_bm25,
) -> list[RetrievedClause]:
    cfg = config or SearchConfig()
    rankings: list[list[ChunkHit]] = []
    if cfg.mode in {"bm25", "hybrid", "rerank"}:
        rankings.append(index_factory(conn, tenant_id, cfg.strategy).search(query, cfg.candidates))
    if cfg.mode in {"dense", "hybrid", "rerank"}:
        if embedder is None:
            raise ValueError(f"mode {cfg.mode} needs an embedder (install the ml extra)")
        rankings.append(
            dense_search(conn, embedder.encode_query(query), cfg.strategy, cfg.candidates)
        )
    hits = rankings[0] if len(rankings) == 1 else rrf(rankings)
    rows = hydrate_chunks(conn, hits, as_of)
    if cfg.mode == "rerank":
        if reranker is None:
            raise ValueError("mode rerank needs a reranker (install the ml extra)")
        head = rows[: cfg.rerank_candidates]
        scores = reranker.score(query, [r["match_text"] for r in head])
        rows = [
            {**row, "score": score}
            for row, score in sorted(
                zip(head, scores, strict=True), key=lambda pair: (-pair[1], pair[0]["chunk_id"])
            )
        ]
    clauses = clauses_for_chunks(conn, rows)
    return [
        RetrievedClause(clause=clause, score=score, rank=rank)
        for rank, (clause, score) in enumerate(clauses[:k], start=1)
    ]
