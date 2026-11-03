"""T&Cs and credit config can't silently drift apart."""

from datetime import date

import pytest

from adjudicate.credit.rules import all_policies, load_policy
from adjudicate.ingest.chunkers import STRATEGIES, chunk_document
from adjudicate.ingest.pipeline import parse_corpus

DOCS = parse_corpus()
ALL_CLAUSES = {c.clause_id: c for d in DOCS for c in d.clauses}


def test_every_authorising_clause_exists() -> None:
    for policy in all_policies():
        for rule in policy.rules:
            for clause_id in rule.authorising_clauses:
                assert clause_id in ALL_CLAUSES, f"{policy.tenant_id}/{rule.rule_id}: {clause_id}"


def test_rules_only_cite_their_own_tenants_documents() -> None:
    for policy in all_policies():
        for rule in policy.rules:
            for clause_id in rule.authorising_clauses:
                assert clause_id.startswith(policy.tenant_id)


def test_clause_offsets_point_at_the_clause_text() -> None:
    for doc in DOCS:
        for clause in doc.clauses:
            assert doc.full_text[clause.char_start : clause.char_end] == clause.text


def test_clause_ids_are_unique() -> None:
    ids = [c.clause_id for d in DOCS for c in d.clauses]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_chunks_have_valid_spans_and_unique_ids(strategy: str) -> None:
    for doc in DOCS:
        chunks = chunk_document(doc, strategy)
        assert chunks
        assert len({c.chunk_id for c in chunks}) == len(chunks)
        for chunk in chunks:
            assert 0 <= chunk.char_start < chunk.char_end <= len(doc.full_text)


def test_rule_versions_follow_the_date() -> None:
    baobab = load_policy("baobab")
    march = baobab.rule("bill_shock_over_cap", date(2026, 3, 15))
    may = baobab.rule("bill_shock_over_cap", date(2026, 5, 15))
    assert (march.cap_cents, may.cap_cents) == (50000, 75000)  # pyright: ignore[reportOptionalMemberAccess]


def test_tenants_differ_in_decision_changing_ways() -> None:
    karoo, fynbos = load_policy("karoo"), load_policy("fynbos")
    on = date(2026, 5, 1)
    assert karoo.rule("bill_shock_over_cap", on) is None
    assert fynbos.rule("oob_without_consent", on) is None
    assert load_policy("baobab").rule("bill_shock_over_cap", on) is not None
