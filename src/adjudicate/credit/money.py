"""Integer money arithmetic."""

from datetime import date

# (effective_from, percent). SA's standard rate stayed 15% after the 2025 increases were reversed.
VAT_RATES: list[tuple[date, int]] = [(date(2018, 4, 1), 15)]


def div_half_up(numerator: int, denominator: int) -> int:
    """Non-negative integer division rounded half up, with no floats involved."""
    if numerator < 0 or denominator <= 0:
        raise ValueError("div_half_up needs numerator >= 0 and denominator > 0")
    return (2 * numerator + denominator) // (2 * denominator)


def vat_rate_on(on: date) -> int:
    rate = None
    for start, percent in VAT_RATES:
        if start <= on:
            rate = percent
    if rate is None:
        raise ValueError(f"no VAT rate configured for {on}")
    return rate


def vat_inside(inclusive_cents: int, on: date) -> int:
    """The VAT contained in a VAT-inclusive amount."""
    rate = vat_rate_on(on)
    return div_half_up(inclusive_cents * rate, 100 + rate)


def rand(cents: int) -> str:
    """Display form: 123456 -> 'R1,234.56'."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}R{whole:,}.{part:02d}"
