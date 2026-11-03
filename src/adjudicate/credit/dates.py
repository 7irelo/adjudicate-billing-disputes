"""Billing dates: Africa/Johannesburg cut-offs, SA public holidays, notice periods."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

try:
    SAST = ZoneInfo("Africa/Johannesburg")
except Exception:  # noqa: BLE001  Windows without tzdata. SA has a fixed offset and no DST.
    from datetime import timezone

    SAST = timezone(timedelta(hours=2), "SAST")


def local_date(moment: datetime) -> date:
    """The South African calendar date of an aware timestamp."""
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(SAST).date()


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def public_holidays(year: int) -> set[date]:
    """Public Holidays Act 36 of 1994. A holiday on a Sunday is observed on the Monday."""
    fixed = [
        (1, 1),
        (3, 21),
        (4, 27),
        (5, 1),
        (6, 16),
        (8, 9),
        (9, 24),
        (12, 16),
        (12, 25),
        (12, 26),
    ]
    days: set[date] = set()
    for month, day in fixed:
        holiday = date(year, month, day)
        days.add(holiday)
        if holiday.weekday() == 6:
            days.add(holiday + timedelta(days=1))
    easter = _easter(year)
    days.add(easter - timedelta(days=2))  # Good Friday
    days.add(easter + timedelta(days=1))  # Family Day
    return days


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in public_holidays(day.year)


def add_business_days(start: date, count: int) -> date:
    """The count-th business day after start. Day one is the first business day after start."""
    if count < 1:
        raise ValueError("count must be at least 1")
    day = start
    remaining = count
    while remaining:
        day += timedelta(days=1)
        if is_business_day(day):
            remaining -= 1
    return day


def notice_effective_date(request: date, unit: str, count: int) -> date:
    if unit == "business_days":
        return add_business_days(request, count)
    if unit == "calendar_days":
        return request + timedelta(days=count)
    raise ValueError(f"unknown notice unit: {unit}")
