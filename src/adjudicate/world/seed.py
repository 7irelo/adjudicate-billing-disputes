"""Build the world and load it. Runs as the BYPASSRLS admin role, never on a request path."""

import argparse
import json
import os
from pathlib import Path

import psycopg

from adjudicate.config import REPO_ROOT, get_settings
from adjudicate.credit.rules import all_policies
from adjudicate.db import migrate
from adjudicate.world.generator import TABLES, TENANTS, World
from adjudicate.world.scenarios import apply_all

MANIFEST = Path(os.environ.get("ADJUDICATE_MANIFEST", REPO_ROOT / "data" / "world_manifest.json"))
ALL_TABLES = (
    "chunks",
    "clauses",
    "documents",
    "review_items",
    "disputes",
    "credit_note_lines",
    "credit_notes",
    "credit_rules",
    "payments",
    "consents",
    "notifications",
    "cdrs",
    "invoice_lines",
    "invoices",
    "subscription_events",
    "subscriptions",
    "bundles",
    "tariff_rates",
    "tariff_plans",
    "accounts",
    "tenants",
)


def build_world(seed: int, accounts_per_tenant: int) -> World:
    world = World(seed)
    for tenant in TENANTS:
        for _ in range(accounts_per_tenant):
            world.background_account(tenant)
    apply_all(world)
    return world


def manifest(world: World) -> dict:
    return {
        "seed": world.seed,
        "world_hash": world.snapshot_hash(),
        "scenarios": {
            f"{tenant}:{name}": {
                "account_ref": info.account_ref,
                "subscription_id": info.subscription_id,
                "records": info.records,
            }
            for (tenant, name), info in world.scenarios.items()
        },
    }


def load(world: World, dsn: str | None = None) -> None:
    with psycopg.connect(dsn or get_settings().admin_dsn) as conn:
        conn.execute(f"TRUNCATE {', '.join(ALL_TABLES)} CASCADE")  # pyright: ignore[reportArgumentType]
        for table in TABLES:
            rows = world.rows[table]
            if not rows:
                continue
            columns = list(rows[0])
            statement = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
            with conn.cursor().copy(statement) as copy:  # pyright: ignore[reportArgumentType]
                for row in rows:
                    copy.write_row([row[c] for c in columns])
        for policy in all_policies():
            for rule in policy.rules:
                conn.execute(
                    "INSERT INTO credit_rules (tenant_id, rule_id, version, effective_from, "
                    "effective_to, definition) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        policy.tenant_id,
                        rule.rule_id,
                        rule.version,
                        rule.effective_from,
                        rule.effective_to,
                        json.dumps(rule.model_dump(mode="json")),
                    ),
                )
        conn.commit()


def seed(
    accounts_per_tenant: int = 20,
    world_seed: int | None = None,
    owner_dsn: str | None = None,
    admin_dsn: str | None = None,
    write_manifest: bool = True,
) -> World:
    from adjudicate.ingest.pipeline import ingest_all

    migrate(owner_dsn)
    world = build_world(world_seed or get_settings().world_seed, accounts_per_tenant)
    load(world, admin_dsn)
    ingest_all(admin_dsn)
    if write_manifest:
        MANIFEST.write_text(json.dumps(manifest(world), indent=1, sort_keys=True) + "\n", "utf-8")
    return world


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts", type=int, default=20)
    args = parser.parse_args()
    built = seed(args.accounts)
    counts = {t: len(built.rows[t]) for t in ("accounts", "cdrs", "invoices", "invoice_lines")}
    print("seeded", counts, "hash", built.snapshot_hash())
