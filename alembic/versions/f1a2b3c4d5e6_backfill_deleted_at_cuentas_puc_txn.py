"""backfill soft-delete deleted_at on cuentas_puc and transactions_posted

a1b2c3d4e5f6 added deleted_at to transactions_posted/cuentas_puc/chat_sessions/
user_company, but it was inserted mid-history. Databases that had already
migrated PAST that point (e.g. persisted dev volumes, Supabase prod) treat it as
"already applied" and never run it, so those two columns are missing there while
fresh databases (CI) have them. d9e0f1a2b3c4 backfilled chat_sessions/user_company
the same way; this revision backfills the remaining two. Idempotent: skips any
column/index already present, so it is a no-op on fresh databases.

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-06-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ["cuentas_puc", "transactions_posted"]


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
        if not _column_exists(bind, table, "deleted_at"):
            op.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMPTZ NULL")

        index_name = f"ix_{table}_deleted_at"
        if not _index_exists(bind, index_name):
            op.execute(f"CREATE INDEX {index_name} ON {table} (deleted_at)")


def downgrade() -> None:
    bind = op.get_bind()

    for table in reversed(_TABLES):
        index_name = f"ix_{table}_deleted_at"
        if _index_exists(bind, index_name):
            op.execute(f"DROP INDEX {index_name}")

        if _column_exists(bind, table, "deleted_at"):
            op.execute(f"ALTER TABLE {table} DROP COLUMN deleted_at")
