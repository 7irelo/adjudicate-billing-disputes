"""The world must be reproducible, reconcile by construction, and stay visibly synthetic."""

from collections import defaultdict

from adjudicate.world.seed import build_world


def test_same_seed_gives_the_same_world() -> None:
    assert build_world(5, 1).snapshot_hash() == build_world(5, 1).snapshot_hash()


def test_different_seed_gives_a_different_world() -> None:
    assert build_world(5, 1).snapshot_hash() != build_world(6, 1).snapshot_hash()


def test_invoice_totals_equal_their_lines() -> None:
    world = build_world(5, 1)
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for line in world.rows["invoice_lines"]:
        totals[(line["tenant_id"], line["invoice_id"])] += line["amount_cents"]
    for invoice in world.rows["invoices"]:
        assert invoice["total_cents"] == totals[(invoice["tenant_id"], invoice["invoice_id"])]


def test_usage_lines_equal_their_rated_cdrs() -> None:
    world = build_world(5, 1)
    rated: dict[tuple[str, str], int] = defaultdict(int)
    for cdr in world.rows["cdrs"]:
        if cdr["invoice_line_id"]:
            rated[(cdr["tenant_id"], cdr["invoice_line_id"])] += cdr["billed_cents"]
    for line in world.rows["invoice_lines"]:
        if line["line_type"].startswith("usage_"):
            assert line["amount_cents"] == rated[(line["tenant_id"], line["line_id"])]


def test_billed_calls_are_all_on_an_invoice() -> None:
    world = build_world(5, 1)
    for cdr in world.rows["cdrs"]:
        if cdr["billed_cents"] and cdr["direction"] == "outbound":
            assert cdr["invoice_line_id"], cdr["cdr_id"]


def test_msisdns_use_the_unallocated_prefix() -> None:
    for account in build_world(5, 1).rows["accounts"]:
        assert account["msisdn"].startswith("+2700")
