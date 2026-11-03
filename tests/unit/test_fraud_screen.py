"""The fraud screen on generated patterns and their legitimate near twins, with no database."""

import pytest

from adjudicate.fraud.screen import screen
from adjudicate.world.fraud_patterns import (
    add_diaspora_family,
    add_irsf,
    add_low_and_slow,
    add_overseas_client,
    add_pbx_burst,
    add_real_trip,
    add_wangiri,
)
from adjudicate.world.generator import World


def outcome(pattern, tenant: str = "baobab") -> str:
    world = World(1)
    _, sub = world.add_account(tenant)
    pattern(world, tenant, sub)
    calls = [c for c in world.rows["cdrs"] if c["subscription_id"] == sub]
    may = [c for c in calls if c["started_at"].month == 5]
    baseline = [c for c in calls if c["started_at"].month in (2, 3, 4)]
    customer = "business" if tenant == "fynbos" else "consumer"
    return screen(may, baseline, customer).outcome


@pytest.mark.parametrize("pattern", [add_pbx_burst, add_irsf, add_wangiri])
@pytest.mark.parametrize("tenant", ["baobab", "karoo", "fynbos"])
def test_fraud_patterns_are_flagged(pattern, tenant) -> None:
    assert outcome(pattern, tenant) == "flagged"


@pytest.mark.parametrize("pattern", [add_diaspora_family, add_real_trip])
def test_legitimate_twins_are_clear(pattern) -> None:
    assert outcome(pattern) == "clear"


def test_overseas_client_is_at_most_a_weak_signal() -> None:
    assert outcome(add_overseas_client, "fynbos") in {"clear", "weak"}


def test_low_and_slow_evades_the_screen() -> None:
    """A known limit, kept visible: this pattern is built to stay under the thresholds."""
    assert outcome(add_low_and_slow) == "clear"


def test_customer_language_cannot_trigger_the_screen() -> None:
    """The screen's signature has no text parameter, so saying 'fraud' can't flag anything."""
    import inspect

    assert "text" not in inspect.signature(screen).parameters
