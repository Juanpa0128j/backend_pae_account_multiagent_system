"""add ica and provision_renta columns to transactions_posted

Revision ID: f3a4b5c6d7e8
Revises: e1a2f3b4c5d6
Create Date: 2026-04-02 21:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e1a2f3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists("transactions_posted", "ica"):
        op.add_column(
            "transactions_posted",
            sa.Column("ica", sa.Numeric(15, 2), server_default="0"),
        )
    if not _column_exists("transactions_posted", "provision_renta"):
        op.add_column(
            "transactions_posted",
            sa.Column("provision_renta", sa.Numeric(15, 2), server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("transactions_posted", "provision_renta")
    op.drop_column("transactions_posted", "ica")
