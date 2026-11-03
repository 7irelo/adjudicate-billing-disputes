"""Load the committed corpus into Postgres. Embeddings are added later, only if the ML extra exists."""

from pathlib import Path

import psycopg

from adjudicate.config import REPO_ROOT, get_settings
from adjudicate.ingest.chunkers import STRATEGIES, chunk_document
from adjudicate.ingest.clause_tree import ParsedDocument, parse_document

CORPUS = REPO_ROOT / "corpus"


def corpus_files() -> list[Path]:
    return sorted(CORPUS.glob("tenants/*/*.md")) + sorted(CORPUS.glob("global/*.md"))


def parse_corpus() -> list[ParsedDocument]:
    return [parse_document(path) for path in corpus_files()]


def ingest_all(dsn: str | None = None) -> int:
    """Replace the corpus tables with the committed documents. Returns the clause count."""
    documents = parse_corpus()
    with psycopg.connect(dsn or get_settings().admin_dsn) as conn:
        conn.execute("TRUNCATE chunks, clauses, documents")
        total = 0
        for doc in documents:
            conn.execute(
                "INSERT INTO documents (document_id, tenant_id, title, kind, effective_from, "
                "effective_to, source_sha256, full_text) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    doc.document_id,
                    doc.tenant_id,
                    doc.title,
                    doc.kind,
                    doc.effective_from,
                    doc.effective_to,
                    doc.sha256,
                    doc.full_text,
                ),
            )
            for c in doc.clauses:
                conn.execute(
                    "INSERT INTO clauses (clause_id, document_id, tenant_id, kind, path, text, "
                    "effective_from, effective_to, char_start, char_end) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        c.clause_id,
                        doc.document_id,
                        doc.tenant_id,
                        c.kind,
                        c.path,
                        c.text,
                        c.effective_from,
                        c.effective_to,
                        c.char_start,
                        c.char_end,
                    ),
                )
                total += 1
            for strategy in STRATEGIES:
                for chunk in chunk_document(doc, strategy):
                    conn.execute(
                        "INSERT INTO chunks (chunk_id, strategy, document_id, tenant_id, text, "
                        "match_text, char_start, char_end, effective_from, effective_to) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            chunk.chunk_id,
                            strategy,
                            doc.document_id,
                            doc.tenant_id,
                            chunk.text,
                            chunk.match_text,
                            chunk.char_start,
                            chunk.char_end,
                            doc.effective_from,
                            doc.effective_to,
                        ),
                    )
        conn.commit()
    return total
