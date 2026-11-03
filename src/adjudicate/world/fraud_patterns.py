"""CDR patterns for fraud and its legitimate near twins.

Each function adds calls and returns {record_key: [cdr_ids]}. Baselines come from a quiet Feb-Apr.
A screen tuned to these patterns will overfit to them, so thresholds are tuned on one seed and
reported on another, and low-and-slow is expected to get past the screen sometimes.
"""

from adjudicate.world.generator import World, sast

PREMIUM = "+882 16 555"
OVERNIGHT_HOURS = (0, 1, 2, 3, 4)


def _baseline(w: World, tenant: str, sub: str, per_month: int = 1) -> None:
    for month in (2, 3, 4):
        for i in range(per_month):
            w.add_call(
                tenant,
                sub,
                sast(2026, month, 5 + i, 14),
                180,
                f"+44 20 7946 {100 + i:04d}",
                "international",
            )


def add_pbx_burst(w: World, tenant: str, sub: str) -> dict:
    """A compromised PBX: dozens of long overnight calls to sequential numbers."""
    _baseline(w, tenant, sub)
    cdrs = [
        w.add_call(
            tenant,
            sub,
            sast(2026, 5, 14, OVERNIGHT_HOURS[i % 5], (i * 7) % 60),
            480,
            f"{PREMIUM} {4400 + i:04d}",
            "premium",
        )
        for i in range(40)
    ]
    return {"burst": cdrs}


def add_irsf(w: World, tenant: str, sub: str) -> dict:
    """International revenue share fraud: long calls to a high-risk range, not overnight only."""
    _baseline(w, tenant, sub)
    cdrs = [
        w.add_call(
            tenant,
            sub,
            sast(2026, 5, 16 + i % 3, 9 + i % 9, (i * 11) % 60),
            900,
            f"{PREMIUM} {7000 + i * 3:04d}",
            "premium",
        )
        for i in range(15)
    ]
    return {"burst": cdrs}


def add_wangiri(w: World, tenant: str, sub: str) -> dict:
    """One-ring missed calls from abroad, then the customer calls back a premium number."""
    _baseline(w, tenant, sub)
    missed, callbacks = [], []
    for i in range(6):
        number = f"{PREMIUM} {8100 + i:04d}"
        missed.append(
            w.add_call(
                tenant,
                sub,
                sast(2026, 5, 11 + i, 18),
                3,
                number,
                "premium",
                direction="inbound_missed",
            )
        )
        callbacks.append(w.add_call(tenant, sub, sast(2026, 5, 11 + i, 19), 240, number, "premium"))
    return {"missed": missed, "callbacks": callbacks}


def add_low_and_slow(w: World, tenant: str, sub: str) -> dict:
    """Evasion: a few daytime calls spread out to stay under thresholds, on ordinary prefixes."""
    _baseline(w, tenant, sub)
    cdrs = [
        w.add_call(
            tenant,
            sub,
            sast(2026, 5, 2 + i * 5, 11 + i % 5),
            420,
            f"+371 67 555 {100 + i * 17:04d}",
            "international",
        )
        for i in range(5)
    ]
    return {"burst": cdrs}


def add_diaspora_family(w: World, tenant: str, sub: str) -> dict:
    """A family calling home every month, so the disputed month is not a spike against baseline."""
    for month in (2, 3, 4):
        for i in range(6):
            w.add_call(
                tenant,
                sub,
                sast(2026, month, 3 + i * 4, 19),
                900,
                "+263 24 2700 1234",
                "international",
            )
    cdrs = [
        w.add_call(
            tenant, sub, sast(2026, 5, 3 + i * 4, 19), 900, "+263 24 2700 1234", "international"
        )
        for i in range(7)
    ]
    return {"calls": cdrs}


def add_overseas_client(w: World, tenant: str, sub: str) -> dict:
    """A business with a real overseas client: more calls this month, in office hours, one number."""
    for month in (2, 3, 4):
        for i in range(4):
            w.add_call(
                tenant,
                sub,
                sast(2026, month, 3 + i * 5, 10),
                600,
                "+1 202 555 0147",
                "international",
            )
    cdrs = [
        w.add_call(
            tenant,
            sub,
            sast(2026, 5, 2 + i * 2, 10 + i % 6),
            600,
            "+1 202 555 0147",
            "international",
        )
        for i in range(12)
    ]
    return {"calls": cdrs}


def add_real_trip(w: World, tenant: str, sub: str) -> dict:
    """A genuine holiday: daytime roaming calls home over one week."""
    cdrs = [
        w.add_call(tenant, sub, sast(2026, 5, 5 + i, 9 + i % 10), 300, "+27 21 555 0199", "roaming")
        for i in range(7)
    ]
    return {"calls": cdrs}
