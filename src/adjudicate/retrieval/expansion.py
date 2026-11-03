"""Query rewriting and definition expansion. Deterministic, so they cost nothing and can be tested.

Rewriting appends policy language to how customers talk. It never replaces their words, because
the original terms can still be the best match. Definition expansion adds the clause defining a
term that a retrieved clause uses, because a condition often lives in a definition.
"""

import re

from adjudicate.db import DictConnection
from adjudicate.schemas import RetrievedClause

# Customer phrasing -> the words the T&Cs use.
_REWRITES: tuple[tuple[str, str], ...] = (
    (
        r"\bcancell?ed\b|\bterminated\b|\bclosed (my|our|the) account\b",
        "cancellation effective date notice",
    ),
    (
        r"\b(twice|two times|double[- ]?(billed|charged)?|duplicate)\b",
        "same charge billed more than once duplicate credit",
    ),
    (
        r"\b(never|didn'?t|did not) (opt|agree|consent|choose|chose)",
        "opt-in active consent reversal",
    ),
    (
        r"\bout[- ]of[- ]bundle\b|\bbundle (ran out|finished|depleted|ended)\b|\bminutes ran out\b",
        "out-of-bundle usage opt-in depletion",
    ),
    (r"\b(overseas|abroad|holiday|trip)\b", "roaming activation international"),
    (
        r"\b(hacked|cloned|stolen|not mine|aren'?t mine|never made|don'?t recognise)\b",
        "suspected fraud unauthorised use compromise",
    ),
    (r"\b(warn(ed)?|sms|huge|bill shock)\b", "bill-shock threshold SMS usage charges"),
    (
        r"\b(too late|old invoice|months ago|how long)\b",
        "query window dispute period after invoice issued",
    ),
    (r"\b(per minute|rate|tariff|overcharged)\b", "rated above tariff in force credit difference"),
    (r"\b(unhappy|complain|escalate)\b", "escalate outcome authority"),
    (r"\b(without a manager|approve|approval)\b", "credits approved automatically limit"),
)
_COMPILED = tuple((re.compile(p, re.I), add) for p, add in _REWRITES)
_DEFINED_TERM = re.compile(r'^"([^"]+)"')


def rewrite_query(query: str) -> str:
    additions = [add for pattern, add in _COMPILED if pattern.search(query)]
    return " ".join([query, *additions]) if additions else query


def with_definitions(conn: DictConnection, hits: list[RetrievedClause]) -> list[RetrievedClause]:
    """Append definitions of terms that retrieved clauses use. Rank order of hits is preserved."""
    if not hits:
        return hits
    from adjudicate.retrieval.pipeline import row_to_clause

    documents = sorted({h.clause.clause_id.split(":")[0] for h in hits})
    rows = conn.execute(
        "SELECT * FROM clauses WHERE document_id = ANY(%s) AND kind = 'definition'", (documents,)
    ).fetchall()
    seen = {h.clause.clause_id for h in hits}
    rank = len(hits)
    out = list(hits)
    for row in rows:
        match = _DEFINED_TERM.match(row["text"])
        if not match or row["clause_id"] in seen:
            continue
        term = match.group(1).lower()
        document = row["document_id"]
        used = any(
            h.clause.clause_id.startswith(f"{document}:") and term in h.clause.text.lower()
            for h in hits
        )
        if used:
            rank += 1
            seen.add(row["clause_id"])
            # Score 0 marks it as supplied by code, so it doesn't inflate the gate's clause score.
            out.append(RetrievedClause(clause=row_to_clause(row), score=0.0, rank=rank))
    return out
