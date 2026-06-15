"""add soft-delete deleted_at to chat_sessions and user_company

These two tables were created after the initial soft-delete migration
(a1b2c3d4e5f6) ran, so they couldn't be covered there.

Revision ID: d9e0f1a2b3c4
Revises: c2d3e4f5a6b7
Create Date: 2026-06-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ["chat_sessions", "user_company"]


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
