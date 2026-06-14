"""add soft-delete deleted_at column to transactions_posted, chat_sessions, cuentas_puc, user_company

Idempotent: column and index existence checked before applying.

Revision ID: a1b2c3d4e5f6
Revises: 8fb1b0855393
Create Date: 2026-06-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = [
    "transactions_posted",
    "chat_sessions",
    "cuentas_puc",
    "user_company",
]


def _table_exists(bind, table: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (table,),
        ).first()
        is not None
    )


def _column_exists(bind, table: str, column: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
            (table, column),
        ).first()
        is not None
    )


def _index_exists(bind, index_name: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname=%s",
            (index_name,),
        ).first()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()

    for table in _TABLES:
        if not _table_exists(bind, table):
            continue

        if not _column_exists(bind, table, "deleted_at"):
            op.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMPTZ NULL")

        index_name = f"ix_{table}_deleted_at"
        if not _index_exists(bind, index_name):
            op.execute(f"CREATE INDEX {index_name} ON {table} (deleted_at)")


def downgrade() -> None:
    bind = op.get_bind()

    for table in reversed(_TABLES):
        if not _table_exists(bind, table):
            continue

        index_name = f"ix_{table}_deleted_at"
        if _index_exists(bind, index_name):
            op.execute(f"DROP INDEX {index_name}")

        if _column_exists(bind, table, "deleted_at"):
            op.execute(f"ALTER TABLE {table} DROP COLUMN deleted_at")
