"""Retrieval scoring, worked by hand, plus the fusion and span logic it relies on."""

import math

import pytest

from adjudicate.retrieval.pipeline import ChunkHit, rrf
from evals.retrieval_eval import gold_ranks, score_query

SPANS = {"g1": ("doc", 0, 100), "g2": ("doc", 200, 300)}


def chunk(chunk_id: str, start: int, end: int, document_id: str = "doc") -> dict:
    return {"chunk_id": chunk_id, "document_id": document_id, "char_start": start, "char_end": end}


def test_ndcg_never_exceeds_one_when_one_chunk_holds_all_gold() -> None:
    whole_document = chunk("doc#A0", 0, 1000)
    ranks = gold_ranks([whole_document], ["g1", "g2"], SPANS)
    assert ranks == {"g1": 1, "g2": 1}
    assert score_query(ranks, ["g1", "g2"])["ndcg@10"] == pytest.approx(1.0)


def test_scores_worked_by_hand() -> None:
    scores = score_query({"g1": 2}, ["g1", "g2"])
    assert scores["recall@1"] == 0
    assert scores["recall@3"] == 0.5
    assert scores["mrr"] == 0.5
    ideal = 1 / math.log2(2) + 1 / math.log2(3)
    assert scores["ndcg@10"] == pytest.approx((1 / math.log2(3)) / ideal)


def test_nothing_found_scores_zero() -> None:
    scores = score_query({}, ["g1"])
    assert scores["recall@10"] == scores["mrr"] == scores["ndcg@10"] == 0


def test_small_overlap_does_not_count() -> None:
    assert gold_ranks([chunk("c1", 90, 150)], ["g1"], SPANS) == {}
    assert gold_ranks([chunk("c1", 40, 150)], ["g1"], SPANS) == {"g1": 1}


def test_other_documents_do_not_count() -> None:
    assert gold_ranks([chunk("c1", 0, 100, "other")], ["g1"], SPANS) == {}


def test_small_to_big_counts_only_the_matched_clause() -> None:
    """E returns a whole section, but finding one clause isn't finding its siblings."""
    assert gold_ranks([chunk("g1#E", 0, 300)], ["g1", "g2"], SPANS) == {"g1": 1}


def test_rrf_worked_by_hand() -> None:
    fused = rrf([[ChunkHit("a", 9), ChunkHit("b", 8)], [ChunkHit("b", 5), ChunkHit("c", 4)]], k=60)
    assert [h.chunk_id for h in fused] == ["b", "a", "c"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)


def test_rrf_ties_break_on_id() -> None:
    fused = rrf([[ChunkHit("z", 1)], [ChunkHit("a", 1)]])
    assert [h.chunk_id for h in fused] == ["a", "z"]
