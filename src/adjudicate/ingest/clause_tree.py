"""Parse an authored document into clauses with stable IDs and character offsets.

Format: YAML front matter; `## N. Title [section_key]` headings; clauses as `N.M text {kind}`.
Clause IDs look like `karoo-tcs-2025:cancellation:5.2` and offsets index into `full_text`.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from adjudicate.schemas import Clause

_FRONT = re.compile(r"\A---\n(.*?)\n---\n+", re.DOTALL)
_SECTION = re.compile(r"^## (\d+)\. (.+?) \[(\w+)\]$", re.MULTILINE)
_CLAUSE = re.compile(r"^(\d+\.\d+) (.+?) \{(\w+)\}$", re.MULTILINE)
_KINDS = {"grant", "limit", "condition", "definition", "procedure", "other"}


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    tenant_id: str | None
    title: str
    kind: str
    effective_from: date
    effective_to: date | None
    sha256: str
    full_text: str
    clauses: list[Clause]


def parse_document(path: Path) -> ParsedDocument:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    front = _FRONT.match(raw)
    if not front:
        raise ValueError(f"{path}: missing front matter")
    meta = yaml.safe_load(front.group(1))
    body = raw[front.end() :]

    tenant_id = meta.get("tenant")
    document_id = meta["document_id"]
    clauses: list[Clause] = []
    sections = list(_SECTION.finditer(body))
    for index, section in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(body)
        number, title, key = section.group(1), section.group(2), section.group(3)
        for match in _CLAUSE.finditer(body, section.end(), end):
            clause_number, kind = match.group(1), match.group(3)
            if kind not in _KINDS:
                raise ValueError(f"{path}: clause {clause_number} has unknown kind {kind!r}")
            if not clause_number.startswith(f"{number}."):
                raise ValueError(f"{path}: clause {clause_number} is under section {number}")
            clauses.append(
                Clause(
                    clause_id=f"{document_id}:{key}:{clause_number}",
                    scope="tenant" if tenant_id else "global",
                    kind=kind,  # pyright: ignore[reportArgumentType]
                    path=[meta["title"], f"{number}. {title}", clause_number],
                    text=match.group(2),
                    effective_from=meta["effective_from"],
                    effective_to=meta.get("effective_to"),
                    char_start=match.start(2),
                    char_end=match.end(2),
                )
            )
    if not clauses:
        raise ValueError(f"{path}: no clauses found")
    return ParsedDocument(
        document_id=document_id,
        tenant_id=tenant_id,
        title=meta["title"],
        kind=meta["kind"],
        effective_from=meta["effective_from"],
        effective_to=meta.get("effective_to"),
        sha256=hashlib.sha256(raw.encode()).hexdigest(),
        full_text=body,
        clauses=clauses,
    )
