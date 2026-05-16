"""add_file_names_to_ingest_jobs

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-05-14 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "n2o3p4q5r6s7"
down_revision: Union[str, Sequence[str], None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ingest_jobs",
        sa.Column(
            "file_names",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="List of all uploaded file names",
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_jobs", "file_names")
