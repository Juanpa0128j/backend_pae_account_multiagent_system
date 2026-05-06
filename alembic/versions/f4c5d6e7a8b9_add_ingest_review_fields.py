"""add ingest review fields

Revision ID: f4c5d6e7a8b9
Revises: f2c3d4e5f6a7
Create Date: 2026-05-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4c5d6e7a8b9"
down_revision: Union[str, Sequence[str], None] = "f2c3d4e5f6a7"
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
    op.execute("ALTER TYPE ingeststatus ADD VALUE IF NOT EXISTS 'PENDING_REVIEW'")

    if not _column_exists("ingest_jobs", "classification_confirmed"):
        op.add_column(
            "ingest_jobs",
            sa.Column(
                "classification_confirmed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    if not _column_exists("ingest_jobs", "classification_confidence"):
        op.add_column(
            "ingest_jobs",
            sa.Column(
                "classification_confidence",
                sa.Numeric(4, 3),
                nullable=True,
            ),
        )


def downgrade() -> None:
    if _column_exists("ingest_jobs", "classification_confidence"):
        op.drop_column("ingest_jobs", "classification_confidence")
    if _column_exists("ingest_jobs", "classification_confirmed"):
        op.drop_column("ingest_jobs", "classification_confirmed")
