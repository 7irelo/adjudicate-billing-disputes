"""Connections and migrations. `TenantPool.tenant_conn` is how request paths reach the database."""

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from adjudicate.config import REPO_ROOT, get_settings

MIGRATIONS = REPO_ROOT / "db" / "migrations"
DictConnection = psycopg.Connection[dict[str, Any]]


def migrate(dsn: str | None = None, migrations_dir: Path = MIGRATIONS) -> list[str]:
    """Apply unapplied migrations in filename order, as the owner role."""
    applied: list[str] = []
    with psycopg.connect(dsn or get_settings().owner_dsn, autocommit=False) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name not in done:
                conn.execute(path.read_text(encoding="utf-8"))  # pyright: ignore[reportArgumentType, reportCallIssue]
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                applied.append(path.name)
        conn.commit()
    return applied


class TenantPool:
    """A pool on the non-owner app role. Tenant context lives only inside one transaction."""

    def __init__(self, dsn: str | None = None, min_size: int = 1, max_size: int = 5) -> None:
        self.pool: ConnectionPool[DictConnection] = ConnectionPool(
            dsn or get_settings().app_dsn,
            connection_class=psycopg.Connection[dict[str, Any]],
            min_size=min_size,
            max_size=max_size,
            open=True,
            kwargs={"row_factory": dict_row},
        )

    @contextmanager
    def tenant_conn(self, tenant_id: str) -> Iterator[DictConnection]:
        """Yield a connection in a transaction with a transaction-local tenant setting.

        `set_config(..., true)` is `SET LOCAL`, so returning the connection to the pool clears the
        context, including when the transaction rolls back.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required")
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            yield conn

    def close(self) -> None:
        self.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate"])
    parser.parse_args()
    print("applied:", migrate() or "nothing to do")
