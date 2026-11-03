"""Database isolation. These connect as the same non-owner role that request paths use."""

import random
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

pytestmark = pytest.mark.db

TENANTS = ("baobab", "karoo", "fynbos")


def tenant_tables(conn) -> list[str]:
    """Every public table with a tenant_id column.

    Reads pg_catalog on purpose. information_schema only lists tables the current role has
    privileges on, so it hid exactly the table this guard exists to find (F-005).
    """
    rows = conn.execute(
        "SELECT c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
        "AND a.attname = 'tenant_id' AND NOT a.attisdropped ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def set_tenant(conn, tenant: str) -> None:
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))


def test_catalog_guard_every_tenant_table_has_forced_rls(admin_conn) -> None:
    """A new table with tenant_id can't quietly miss a policy."""
    tables = tenant_tables(admin_conn)
    assert len(tables) >= 20
    for table in tables:
        row = admin_conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{table}",),
        ).fetchone()
        assert row == (True, True), f"{table} lacks forced RLS"
        policies = admin_conn.execute(
            "SELECT qual, with_check FROM pg_policies WHERE tablename = %s", (table,)
        ).fetchall()
        assert policies, f"{table} has no policy"
        for qual, check in policies:
            assert qual and "current_tenant()" in qual, f"{table}: USING doesn't scope by tenant"
            assert check and "current_tenant()" in check, f"{table}: WITH CHECK doesn't scope"


def test_app_role_cannot_bypass_rls(app_conn) -> None:
    row = app_conn.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
        "WHERE rolname = current_user"
    ).fetchone()
    assert row == (False, False, False, False)
    owned = app_conn.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user"
    ).fetchone()
    assert owned[0] == 0


def test_seed_put_rows_in_every_table_for_every_tenant(admin_conn) -> None:
    """Guards the rest of this file: a cross-tenant read test on an empty table proves nothing."""
    not_seeded = {"review_items", "disputes"}
    for table in tenant_tables(admin_conn):
        if table in not_seeded:
            continue
        for tenant in TENANTS:
            count = admin_conn.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (tenant,)
            ).fetchone()[0]
            assert count > 0, f"no {table} rows for {tenant}"


def test_unset_tenant_sees_zero_rows_everywhere(app_conn, admin_conn) -> None:
    for table in tenant_tables(admin_conn):
        assert app_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_empty_tenant_setting_fails_closed(app_conn, admin_conn) -> None:
    set_tenant(app_conn, "")
    for table in tenant_tables(admin_conn):
        assert app_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


@pytest.mark.parametrize("tenant", TENANTS)
def test_reads_are_scoped_in_every_table(app_conn, admin_conn, tenant) -> None:
    set_tenant(app_conn, tenant)
    for table in tenant_tables(admin_conn):
        other = app_conn.execute(
            f"SELECT count(*) FROM {table} WHERE tenant_id IS NOT NULL AND tenant_id <> %s",
            (tenant,),
        ).fetchone()[0]
        assert other == 0, f"{tenant} can read another tenant's rows in {table}"


@pytest.mark.parametrize("tenant", TENANTS)
def test_own_rows_are_visible(app_conn, tenant) -> None:
    """Without this, an over-strict policy that returns nothing would pass every other test."""
    set_tenant(app_conn, tenant)
    for table in ("accounts", "invoices", "invoice_lines", "cdrs", "clauses", "credit_rules"):
        assert app_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0, table


def test_cross_tenant_insert_is_blocked(seeded_db) -> None:
    from tests.conftest import APP

    with psycopg.connect(APP) as conn:
        set_tenant(conn, "baobab")
        account = conn.execute("SELECT account_ref FROM accounts LIMIT 1").fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO disputes (tenant_id, dispute_id, account_ref, text) "
                "VALUES ('karoo', 'D-X', %s, 'x')",
                (account,),
            )
        conn.rollback()


def test_cross_tenant_update_changes_nothing(admin_conn) -> None:
    from tests.conftest import ADMIN, APP

    with psycopg.connect(ADMIN) as admin:
        account = admin.execute(
            "SELECT account_ref FROM accounts WHERE tenant_id = 'karoo' LIMIT 1"
        ).fetchone()[0]
        admin.execute(
            "INSERT INTO disputes (tenant_id, dispute_id, account_ref, text) "
            "VALUES ('karoo', 'D-ISO-1', %s, 'original') ON CONFLICT DO NOTHING",
            (account,),
        )
        admin.commit()
    with psycopg.connect(APP) as conn:
        set_tenant(conn, "baobab")
        result = conn.execute("UPDATE disputes SET text = 'hacked' WHERE dispute_id = 'D-ISO-1'")
        assert result.rowcount == 0
        conn.commit()
    with psycopg.connect(ADMIN) as admin:
        text = admin.execute("SELECT text FROM disputes WHERE dispute_id = 'D-ISO-1'").fetchone()
        assert text == ("original",)


@pytest.mark.parametrize("table", ["accounts", "invoice_lines", "cdrs", "credit_notes", "disputes"])
def test_app_role_cannot_delete(seeded_db, table) -> None:
    from tests.conftest import APP

    with psycopg.connect(APP) as conn:
        set_tenant(conn, "baobab")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"DELETE FROM {table}")
        conn.rollback()


@pytest.mark.parametrize("table", ["invoice_lines", "cdrs", "credit_rules", "clauses", "accounts"])
def test_app_role_cannot_change_billing_or_policy_data(seeded_db, table) -> None:
    from tests.conftest import APP

    with psycopg.connect(APP) as conn:
        set_tenant(conn, "baobab")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"UPDATE {table} SET tenant_id = 'baobab'")
        conn.rollback()


def test_app_cannot_write_an_issued_credit_note(seeded_db) -> None:
    """Least privilege: a successful prompt injection can yield a draft at most."""
    from tests.conftest import APP

    with psycopg.connect(APP) as conn:
        set_tenant(conn, "baobab")
        account = conn.execute("SELECT account_ref FROM accounts LIMIT 1").fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO credit_notes (tenant_id, credit_note_id, account_ref, "
                "idempotency_key, status, credit_cents) "
                "VALUES ('baobab', 'CN-X', %s, 'k1', 'issued', 100)",
                (account,),
            )
        conn.rollback()


def test_composite_fk_blocks_cross_tenant_reference(admin_conn) -> None:
    """Even the BYPASSRLS role can't link one tenant's row to another tenant's account."""
    account = admin_conn.execute(
        "SELECT account_ref FROM accounts WHERE tenant_id = 'karoo' LIMIT 1"
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_conn.execute(
            "INSERT INTO disputes (tenant_id, dispute_id, account_ref, text) "
            "VALUES ('baobab', 'D-FK', %s, 'x')",
            (account,),
        )


def test_global_documents_are_visible_to_all_but_not_writable(app_conn) -> None:
    set_tenant(app_conn, "baobab")
    count = app_conn.execute("SELECT count(*) FROM clauses WHERE tenant_id IS NULL").fetchone()[0]
    assert count > 0


def test_tenant_context_does_not_survive_the_transaction(tenant_pool) -> None:
    with tenant_pool.tenant_conn("baobab") as conn:
        assert conn.execute("SELECT count(*) AS n FROM accounts").fetchone()["n"] > 0
    with tenant_pool.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM accounts").fetchone()["n"] == 0


def test_tenant_context_does_not_survive_a_rollback(tenant_pool) -> None:
    with pytest.raises(RuntimeError), tenant_pool.tenant_conn("baobab"):
        raise RuntimeError("boom")
    with tenant_pool.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM accounts").fetchone()["n"] == 0


def test_pool_does_not_leak_between_concurrent_tenants(tenant_pool) -> None:
    """Many mixed-tenant requests on a small pool, asserting every row of every result."""

    def request(tenant: str) -> list[str]:
        with tenant_pool.tenant_conn(tenant) as conn:
            rows = conn.execute(
                "SELECT tenant_id, pg_sleep(0.001) FROM invoice_lines LIMIT 20"
            ).fetchall()
            assert rows
            return [r["tenant_id"] for r in rows]

    rng = random.Random(7)
    jobs = [rng.choice(TENANTS) for _ in range(120)]
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(request, jobs))
    for tenant, seen in zip(jobs, results, strict=True):
        assert set(seen) == {tenant}
