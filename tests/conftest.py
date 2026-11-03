"""Shared fixtures. DB tests run on a separate `adjudicate_test` database, seeded once per session."""

import os

import psycopg
import pytest

SUPER = os.environ.get(
    "ADJUDICATE_SUPER_DSN", "postgresql://postgres:postgres_dev_pw@localhost:55432/postgres"
)
TEST_DB = os.environ.get("ADJUDICATE_TEST_DB", "adjudicate_test")
HOST = "localhost:55432"
os.environ.setdefault("ADJUDICATE_TOKEN_SECRET", "test-secret-test-secret-test-secret-123")


def dsn(role: str, password: str, db: str = TEST_DB) -> str:
    return f"postgresql://{role}:{password}@{HOST}/{db}"


OWNER = dsn("adjudicate_owner", "owner_dev_pw")
ADMIN = dsn("adjudicate_admin", "admin_dev_pw")
APP = dsn("adjudicate_app", "app_dev_pw")


def create_database(name: str) -> None:
    """Drop and recreate a database with the extension and the app roles' schema access."""
    with psycopg.connect(SUPER, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')  # pyright: ignore[reportArgumentType]
        conn.execute(f'CREATE DATABASE "{name}" OWNER adjudicate_owner')  # pyright: ignore[reportArgumentType]
    target = SUPER.rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("ALTER SCHEMA public OWNER TO adjudicate_owner")
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")


@pytest.fixture(scope="session")
def seeded_db():
    """Migrate and seed, unless the mutation check has prepared and damaged a database already."""
    from adjudicate.world.seed import seed

    if os.environ.get("ADJUDICATE_TEST_DB_READY") == "1":
        yield
        return
    try:
        create_database(TEST_DB)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres is not running (uv run poe db-up): {exc}")
    seed(accounts_per_tenant=2, owner_dsn=OWNER, admin_dsn=ADMIN, write_manifest=False)
    yield


@pytest.fixture(scope="session")
def tenant_pool(seeded_db):
    from adjudicate.db import TenantPool

    pool = TenantPool(APP, min_size=2, max_size=4)
    yield pool
    pool.close()


@pytest.fixture
def app_conn(seeded_db):
    with psycopg.connect(APP) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def admin_conn(seeded_db):
    with psycopg.connect(ADMIN) as conn:
        yield conn
        conn.rollback()
