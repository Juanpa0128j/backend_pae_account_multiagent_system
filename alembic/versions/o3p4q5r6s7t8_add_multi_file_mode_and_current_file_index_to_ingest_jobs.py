"""add_multi_file_mode_and_current_file_index_to_ingest_jobs

Revision ID: o3p4q5r6s7t8
Revises: n2o3p4q5r6s7
Create Date: 2026-05-14 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o3p4q5r6s7t8"
down_revision: Union[str, Sequence[str], None] = "n2o3p4q5r6s7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ingest_jobs",
        sa.Column(
            "multi_file_mode",
            sa.String(20),
            nullable=True,
            comment="'pages' = concatenate as one doc | 'documents' = process each independently",
        ),
    )
    op.add_column(
        "ingest_jobs",
        sa.Column(
            "current_file_index",
            sa.Integer(),
            nullable=True,
            comment="Index of file currently being parsed (0-based), for frontend progress",
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_jobs", "current_file_index")
    op.drop_column("ingest_jobs", "multi_file_mode")
