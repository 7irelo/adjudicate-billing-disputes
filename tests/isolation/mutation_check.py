"""Prove the isolation suite can fail. Tests that have never failed prove little.

Builds a scratch database, runs the suite clean (it must pass), then applies each mutation to a
fresh copy and runs it again (it must fail). Exits non-zero if any mutation survives.
"""

import os
import subprocess
import sys

import psycopg

from tests.conftest import HOST, SUPER, create_database, dsn

SCRATCH = "adjudicate_mutation"
SUITE = ["tests/isolation/test_rls.py"]

# Each is a realistic way for isolation to break.
MUTATIONS = {
    "policy dropped": "DROP POLICY tenant_isolation ON invoice_lines",
    "policy widened to USING (true)": (
        "DROP POLICY tenant_isolation ON cdrs; "
        "CREATE POLICY tenant_isolation ON cdrs USING (true) WITH CHECK (true)"
    ),
    "RLS disabled on a table": "ALTER TABLE accounts DISABLE ROW LEVEL SECURITY",
    "FORCE removed from a table": "ALTER TABLE payments NO FORCE ROW LEVEL SECURITY",
    "new table with tenant_id and no RLS": (
        "CREATE TABLE leaky_notes (tenant_id text, note text); "
        "GRANT SELECT ON leaky_notes TO adjudicate_app"
    ),
    "corpus policy leaks other tenants": (
        "DROP POLICY tenant_or_global ON clauses; "
        "CREATE POLICY tenant_or_global ON clauses USING (current_tenant() IS NOT NULL) "
        "WITH CHECK (tenant_id = current_tenant())"
    ),
    "app role granted BYPASSRLS": "SUPER:ALTER ROLE adjudicate_app BYPASSRLS",
}
UNDO_SUPER = "ALTER ROLE adjudicate_app NOBYPASSRLS"


def prepare() -> None:
    from adjudicate.world.seed import seed

    create_database(SCRATCH)
    seed(
        accounts_per_tenant=1,
        owner_dsn=dsn("adjudicate_owner", "owner_dev_pw", SCRATCH),
        admin_dsn=dsn("adjudicate_admin", "admin_dev_pw", SCRATCH),
        write_manifest=False,
    )


def run_suite() -> int:
    env = {**os.environ, "ADJUDICATE_TEST_DB": SCRATCH, "ADJUDICATE_TEST_DB_READY": "1"}
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *SUITE,
            "-q",
            "-x",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    return process.returncode


def apply(statement: str) -> None:
    if statement.startswith("SUPER:"):
        target = f"postgresql://postgres:postgres_dev_pw@{HOST}/{SCRATCH}"
        statement = statement.removeprefix("SUPER:")
    else:
        target = dsn("adjudicate_owner", "owner_dev_pw", SCRATCH)
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute(statement)  # pyright: ignore[reportArgumentType]


def main() -> int:
    prepare()
    if run_suite() != 0:
        print("FAIL: the suite doesn't pass on a clean database, so the check can't conclude.")
        return 1
    print("clean database: suite passes")

    survivors = []
    try:
        for name, statement in MUTATIONS.items():
            prepare()
            apply(statement)
            code = run_suite()
            caught = code == 1  # pytest: 1 means tests failed; other codes are collection errors
            print(f"{'caught  ' if caught else 'SURVIVED'} {name} (pytest exit {code})")
            if not caught:
                survivors.append(name)
            if statement.startswith("SUPER:"):
                with psycopg.connect(SUPER, autocommit=True) as conn:
                    conn.execute(UNDO_SUPER)  # pyright: ignore[reportArgumentType]
    finally:
        with psycopg.connect(SUPER, autocommit=True) as conn:
            conn.execute(UNDO_SUPER)  # pyright: ignore[reportArgumentType]
            conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')  # pyright: ignore[reportArgumentType]
    if survivors:
        print("FAIL: mutations not detected:", survivors)
        return 1
    print(f"all {len(MUTATIONS)} mutations caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
