"""Parse real PDFs into a clause tree, for the benchmark only. Nothing here is ever served.

pdfplumber rather than PyMuPDF (AGPL, awkward in an MIT repo) or an LLM (cost, non-determinism).
Docling is the plan's first choice, but it pulls in a large model stack for a two-document benchmark,
so it's only worth adding if `parse_check` shows this falling short.
"""

import hashlib
import re
from datetime import date
from pathlib import Path

from adjudicate.ingest.clause_tree import ParsedDocument
from adjudicate.schemas import Clause

_BOILERPLATE = re.compile(
    r"^(Reproduced by Data Dynamics.*|This gazette is also available.*|"
    r"STAATSKOERANT.*|\d+ No\. \d+ GOVERNMENT GAZETTE.*|Page \d+ of \d+|\d+)$"
)
# "8A", "3.11.12", "(1)", or "1." at the start of a line.
_NUMBERED = re.compile(r"^(?P<number>\d+[A-Z]?(?:\.\d+)*\.?|\([0-9a-z]{1,3}\))\s+(?P<rest>\S.*)$")
# Footnotes: "17 Rain Initial Submission 2024 Page 2 Para 1.4". Found by the parse check on GG 53991.
_FOOTNOTE = re.compile(r"^\d+ .*\b(Submission|Page \d+|Para\.? ?\d|Available at|https?://)")


def extract_text(path: Path) -> str:
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            kept = [
                line.strip()
                for line in (page.extract_text() or "").splitlines()
                if line.strip()
                and not _BOILERPLATE.match(line.strip())
                and not _FOOTNOTE.match(line.strip())
            ]
            pages.append("\n".join(kept))
    return "\n".join(pages)


def clauses_from_text(text: str, document_id: str, title: str) -> list[Clause]:
    """Split on numbered lines. Each clause runs until the next numbered line.

    Sub-items like (a) are kept inside their parent clause, because a sub-item usually means nothing
    without the sentence that introduces it.
    """
    starts: list[tuple[int, str]] = []
    offset = 0
    for line in text.split("\n"):
        match = _NUMBERED.match(line)
        if match and not match.group("number").startswith("("):
            starts.append((offset, match.group("number").rstrip(".")))
        offset += len(line) + 1
    clauses: list[Clause] = []
    seen: dict[str, int] = {}
    for index, (start, number) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        body = text[start:end].rstrip()
        if len(body.split()) < 4:
            continue
        seen[number] = seen.get(number, 0) + 1
        suffix = f"~{seen[number]}" if seen[number] > 1 else ""
        clauses.append(
            Clause(
                clause_id=f"{document_id}:{number}{suffix}",
                scope="global",
                kind="other",
                path=[title, number.split(".")[0], number],
                text=body,
                effective_from=date(2016, 1, 1),
                char_start=start,
                char_end=start + len(body),
            )
        )
    return clauses


def parse_pdf(path: Path, document_id: str, title: str) -> ParsedDocument:
    text = extract_text(path)
    return ParsedDocument(
        document_id=document_id,
        tenant_id=None,
        title=title,
        kind="benchmark",
        effective_from=date(2016, 1, 1),
        effective_to=None,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        full_text=text,
        clauses=clauses_from_text(text, document_id, title),
    )


def parse_check(doc: ParsedDocument, sample: int = 30) -> dict:
    """Numbers and a sample to read. Whether boundaries are right still takes a person's eye."""
    lengths = sorted(len(c.text.split()) for c in doc.clauses)
    step = max(1, len(doc.clauses) // sample)
    return {
        "document": doc.document_id,
        "characters": len(doc.full_text),
        "clauses": len(doc.clauses),
        "median_clause_words": lengths[len(lengths) // 2] if lengths else 0,
        "longest_clause_words": lengths[-1] if lengths else 0,
        "offsets_valid": all(
            doc.full_text[c.char_start : c.char_end] == c.text for c in doc.clauses
        ),
        "sample": [c.text[:110] for c in doc.clauses[::step][:sample]],
    }
