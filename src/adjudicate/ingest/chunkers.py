"""The five chunking strategies from the retrieval experiment. Offsets always index full_text.

`match_text` is what gets searched; `text` is what's returned. They differ only for strategy E.
Token counts use whitespace tokens as an approximation, the same for every strategy.
"""

import re
from dataclasses import dataclass

from adjudicate.ingest.clause_tree import ParsedDocument

STRATEGIES = ("A", "B", "C", "D", "E")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    strategy: str
    document_id: str
    match_text: str
    text: str
    char_start: int
    char_end: int


def fixed_tokens(doc: ParsedDocument, size: int = 512, overlap: int = 64) -> list[Chunk]:
    """A: fixed windows of whitespace tokens, ignoring document structure."""
    tokens = list(re.finditer(r"\S+", doc.full_text))
    chunks: list[Chunk] = []
    step = size - overlap
    for index, first in enumerate(range(0, max(len(tokens) - overlap, 1), step)):
        window = tokens[first : first + size]
        start, end = window[0].start(), window[-1].end()
        text = doc.full_text[start:end]
        chunks.append(
            Chunk(f"{doc.document_id}#A{index}", "A", doc.document_id, text, text, start, end)
        )
    return chunks


def _units(text: str, max_tokens: int) -> list[tuple[int, int, int]]:
    """Paragraphs as (start, end, words), falling back to lines for any paragraph that's too long.

    That's the recursive step. PDF text has no blank lines, so a whole document can be one paragraph.
    """
    units: list[tuple[int, int, int]] = []
    for para in re.finditer(r"[^\n]+(?:\n[^\n]+)*", text):
        words = len(para.group().split())
        if words <= max_tokens:
            units.append((para.start(), para.end(), words))
            continue
        for line in re.finditer(r"[^\n]+", para.group()):
            units.append(
                (para.start() + line.start(), para.start() + line.end(), len(line.group().split()))
            )
    return units


def recursive_paragraphs(doc: ParsedDocument, max_tokens: int = 120) -> list[Chunk]:
    """B: merge consecutive paragraphs or lines up to a size limit."""
    chunks: list[Chunk] = []
    start: int | None = None
    end = count = 0

    def flush() -> None:
        text = doc.full_text[start:end]
        chunks.append(
            Chunk(f"{doc.document_id}#B{len(chunks)}", "B", doc.document_id, text, text, start, end)  # pyright: ignore[reportArgumentType]
        )

    for unit_start, unit_end, words in _units(doc.full_text, max_tokens):
        if start is not None and count + words > max_tokens:
            flush()
            start, count = None, 0
        start = unit_start if start is None else start
        end, count = unit_end, count + words
    if start is not None:
        flush()
    return chunks


def clause_chunks(doc: ParsedDocument) -> list[Chunk]:
    """C: one chunk per clause."""
    return [
        Chunk(f"{c.clause_id}#C", "C", doc.document_id, c.text, c.text, c.char_start, c.char_end)
        for c in doc.clauses
    ]


def clause_with_path(doc: ParsedDocument) -> list[Chunk]:
    """D: C with the section path in the searched text, so short clauses keep their topic."""
    chunks = []
    for c in doc.clauses:
        text = f"{c.path[1]} > {c.path[2]}: {c.text}"
        chunks.append(
            Chunk(f"{c.clause_id}#D", "D", doc.document_id, text, text, c.char_start, c.char_end)
        )
    return chunks


def small_to_big(doc: ParsedDocument) -> list[Chunk]:
    """E: match on the clause (with path), return the whole parent section."""
    by_section: dict[str, list] = {}
    for c in doc.clauses:
        by_section.setdefault(c.path[1], []).append(c)
    chunks = []
    for c in doc.clauses:
        siblings = by_section[c.path[1]]
        start, end = siblings[0].char_start, siblings[-1].char_end
        chunks.append(
            Chunk(
                f"{c.clause_id}#E",
                "E",
                doc.document_id,
                f"{c.path[1]} > {c.path[2]}: {c.text}",
                f"{c.path[1]}\n{doc.full_text[start:end]}",
                start,
                end,
            )
        )
    return chunks


CHUNKERS = {
    "A": fixed_tokens,
    "B": recursive_paragraphs,
    "C": clause_chunks,
    "D": clause_with_path,
    "E": small_to_big,
}


def chunk_document(doc: ParsedDocument, strategy: str) -> list[Chunk]:
    return CHUNKERS[strategy](doc)
