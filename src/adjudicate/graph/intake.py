"""Clean dispute text and extract allegations. Claimed values are never used as facts."""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS |= {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b(?:\s+(20\d\d))?", re.I
)
_AMOUNT_RE = re.compile(r"\bR\s?(\d{1,3}(?:[ ,]\d{3})*|\d+)(?:\.(\d{2}))?\b")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

INJECTION_PATTERNS = [
    r"ignore (all |any |the |your |previous |prior )+(instructions|policy|policies|rules|terms)",
    r"(system|developer) prompt",
    r"already approved",
    r"you (must|should|have to) (approve|credit|refund|process)",
    r"just process it",
    r"(other|another) (tenant|operator|customer)'?s?\b",
    r"karoo connect|fynbos voice|baobab mobile",
    r"override",
]
_INJECTION = re.compile("|".join(INJECTION_PATTERNS), re.I)


@dataclass(frozen=True)
class Intake:
    text: str
    claimed_cents: list[int]
    period_start: date
    period_end: date
    period_explicit: bool
    injection_suspected: bool


def clean(text: str) -> str:
    return _CONTROL.sub("", text).strip()


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def default_period(dispute_date: date) -> tuple[date, date]:
    """The latest fully billed month before the dispute."""
    end = dispute_date.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def parse_period(text: str, dispute_date: date) -> tuple[date, date, bool]:
    found: list[tuple[int, int]] = []
    for match in _MONTH_RE.finditer(text):
        word = match.group(1)
        # "may" and "march" are also ordinary words; only trust capitalised forms or a year.
        if word[0].islower() and not match.group(2):
            continue
        month = _MONTHS[word.lower()]
        year = int(match.group(2)) if match.group(2) else dispute_date.year
        if not match.group(2) and date(year, month, 1) > dispute_date:
            year -= 1
        found.append((year, month))
    if not found:
        start, end = default_period(dispute_date)
        return start, end, False
    first, last = min(found), max(found)
    return date(first[0], first[1], 1), _month_end(last[0], last[1]), True


def run_intake(text: str, dispute_date: date, tenant_name: str = "") -> Intake:
    cleaned = clean(text)
    amounts = []
    for whole, part in _AMOUNT_RE.findall(cleaned):
        rand_value = int(re.sub(r"[ ,]", "", whole))
        amounts.append(rand_value * 100 + int(part or 0))
    start, end, explicit = parse_period(cleaned, dispute_date)
    # Naming your own operator isn't suspicious; naming another one is.
    probe = cleaned.replace(tenant_name, "") if tenant_name else cleaned
    return Intake(
        text=cleaned,
        claimed_cents=amounts,
        period_start=start,
        period_end=end,
        period_explicit=explicit,
        injection_suspected=bool(_INJECTION.search(probe)),
    )
