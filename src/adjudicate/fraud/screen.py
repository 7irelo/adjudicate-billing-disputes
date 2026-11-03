"""Signals computed from CDRs only. Dispute text is deliberately not an input.

Thresholds are tuned on one world seed and reported on another.
Passing the near-twin cases matters as much as catching fraud: escalating every international
dispute would otherwise score perfectly.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise

from adjudicate.credit.dates import SAST
from adjudicate.schemas import FraudScreen, FraudSignal

INTERNATIONAL = {"international", "premium", "roaming"}


@dataclass(frozen=True)
class Thresholds:
    high_risk_prefixes: tuple[str, ...] = ("+882", "+881", "+979")
    # Chosen by evals/fraud_eval.py on seed 11 and reported on seed 23. See the summary it writes.
    high_risk_strong_calls: int = 3
    spike_strong_multiple: int = 12
    spike_weak_multiple: int = 3
    spike_min_minutes: int = 45
    off_hours: tuple[int, int] = (0, 5)
    off_hours_burst_calls: int = 15
    sequential_run: int = 8
    wangiri_callbacks: int = 3
    overlap_strong: int = 3


DEFAULT = Thresholds()


def _minutes(rows: list[dict]) -> int:
    return sum(math.ceil(r["duration_seconds"] / 60) for r in rows if r["direction"] == "outbound")


def _trailing_number(destination: str) -> tuple[str, int] | None:
    digits = re.sub(r"\D", "", destination)
    return (digits[:-4], int(digits[-4:])) if len(digits) > 4 else None


def _sequential_run(rows: list[dict]) -> list[str]:
    """The longest run of calls to numbers that step upward within one range."""
    by_range: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in rows:
        parsed = _trailing_number(r["destination"])
        if parsed:
            by_range[parsed[0]].append((parsed[1], r["cdr_id"]))
    best: list[str] = []
    for calls in by_range.values():
        ordered = sorted(set(calls))
        run = [ordered[0]] if ordered else []
        for previous, current in pairwise(ordered):
            run = [*run, current] if 0 < current[0] - previous[0] <= 3 else [current]
            if len(run) > len(best):
                best = [c[1] for c in run]
    return best


def screen(
    rows: list[dict],
    baseline_rows: list[dict],
    customer_type: str,
    baseline_months: int = 3,
    t: Thresholds = DEFAULT,
) -> FraudScreen:
    signals: list[FraudSignal] = []
    outbound_intl = [
        r for r in rows if r["direction"] == "outbound" and r["destination_class"] in INTERNATIONAL
    ]

    high_risk = [r for r in outbound_intl if r["destination"].startswith(t.high_risk_prefixes)]
    if high_risk:
        signals.append(
            FraudSignal(
                name="high_risk_destination",
                strength="strong" if len(high_risk) >= t.high_risk_strong_calls else "weak",
                detail=f"{len(high_risk)} calls to high-risk number ranges",
                record_ids=[r["cdr_id"] for r in high_risk],
            )
        )

    # A roaming trip is not a spike: the customer is abroad, so calls are expected to change.
    spike_rows = [r for r in outbound_intl if r["destination_class"] != "roaming"]
    current = _minutes(spike_rows)
    baseline = _minutes(
        [r for r in baseline_rows if r["destination_class"] in {"international", "premium"}]
    )
    monthly_baseline = baseline // max(baseline_months, 1)
    if current >= t.spike_min_minutes:
        floor = max(monthly_baseline, 1)
        if current >= floor * t.spike_strong_multiple and monthly_baseline < t.spike_min_minutes:
            strength = "strong"
        elif current >= floor * t.spike_weak_multiple:
            strength = "weak"
        else:
            strength = None
        if strength:
            signals.append(
                FraudSignal(
                    name="volume_spike",
                    strength=strength,
                    detail=f"{current} international minutes against a monthly norm of "
                    f"{monthly_baseline}",
                    record_ids=[r["cdr_id"] for r in spike_rows],
                )
            )

    start_hour, end_hour = t.off_hours
    by_night: dict[object, list[dict]] = defaultdict(list)
    for r in outbound_intl:
        local = r["started_at"].astimezone(SAST)
        if start_hour <= local.hour < end_hour:
            by_night[local.date()].append(r)
    for night, group in sorted(by_night.items(), key=lambda kv: str(kv[0])):
        if len(group) >= t.off_hours_burst_calls:
            signals.append(
                FraudSignal(
                    name="off_hours_burst",
                    strength="strong",
                    detail=f"{len(group)} international calls overnight on {night}",
                    record_ids=[r["cdr_id"] for r in group],
                )
            )

    run = _sequential_run(outbound_intl)
    if len(run) >= t.sequential_run:
        signals.append(
            FraudSignal(
                name="sequential_numbers",
                strength="strong",
                detail=f"{len(run)} calls stepping through one number range",
                record_ids=run,
            )
        )

    # Wangiri: a very short missed call from abroad, then a callback to the same number.
    missed = {
        r["destination"]: r
        for r in rows
        if r["direction"] == "inbound_missed" and r["duration_seconds"] <= 10
    }
    callbacks = [
        r
        for r in outbound_intl
        if r["destination"] in missed
        and timedelta(0)
        <= r["started_at"] - missed[r["destination"]]["started_at"]
        <= timedelta(hours=24)
    ]
    if len(callbacks) >= t.wangiri_callbacks:
        signals.append(
            FraudSignal(
                name="wangiri_callbacks",
                strength="strong",
                detail=f"{len(callbacks)} callbacks to numbers that gave a one-ring missed call",
                record_ids=[r["cdr_id"] for r in callbacks]
                + [missed[r["destination"]]["cdr_id"] for r in callbacks],
            )
        )

    # One mobile line can't hold overlapping calls. A business trunk can, so it's not a signal there.
    if customer_type == "consumer":
        ordered = sorted(
            (r for r in rows if r["direction"] == "outbound"), key=lambda r: r["started_at"]
        )
        overlapping: list[str] = []
        latest_end = None
        for r in ordered:
            if latest_end and r["started_at"] < latest_end:
                overlapping.append(r["cdr_id"])
            end = r["started_at"] + timedelta(seconds=r["duration_seconds"])
            latest_end = max(latest_end, end) if latest_end else end
        if overlapping:
            signals.append(
                FraudSignal(
                    name="impossible_usage",
                    strength="strong" if len(overlapping) >= t.overlap_strong else "weak",
                    detail=f"{len(overlapping)} calls overlap on a single line",
                    record_ids=overlapping,
                )
            )

    if any(s.strength == "strong" for s in signals):
        outcome = "flagged"
    elif signals:
        outcome = "weak"
    else:
        outcome = "clear"
    return FraudScreen(outcome=outcome, signals=signals)
