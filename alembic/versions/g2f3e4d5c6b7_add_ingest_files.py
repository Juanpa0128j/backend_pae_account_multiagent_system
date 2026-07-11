"""add ingest_files table for shared upload storage

Revision ID: g2f3e4d5c6b7
Revises: f1e2d3c4b5a6
Create Date: 2026-07-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g2f3e4d5c6b7"
down_revision: Union[str, Sequence[str], None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingest_files",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column(
            "ingest_id",
            sa.String(length=50),
            sa.ForeignKey("ingest_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ingest_files_ingest_id", "ingest_files", ["ingest_id"])
    op.create_index("ix_ingest_files_created_at", "ingest_files", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ingest_files_created_at", table_name="ingest_files")
    op.drop_index("ix_ingest_files_ingest_id", table_name="ingest_files")
    op.drop_table("ingest_files")
