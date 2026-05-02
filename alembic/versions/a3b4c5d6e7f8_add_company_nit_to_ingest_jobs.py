"""add company_nit to ingest_jobs

Revision ID: a3b4c5d6e7f8
Revises: f4c5d6e7a8b9
Create Date: 2026-05-02 11:00:00.000000

Adds a tenant column to ingest_jobs so the company_nit supplied at upload
time is persisted and available as a fallback when the LLM does not extract
nit_receptor from the document (common for internal documents like CEs,
nóminas, dispersiones, extractos bancarios).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "f4c5d6e7a8b9"
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
    if not _column_exists("ingest_jobs", "company_nit"):
        op.add_column(
            "ingest_jobs",
            sa.Column("company_nit", sa.String(20), nullable=True),
        )
        op.create_index(
            "ix_ingest_jobs_company_nit", "ingest_jobs", ["company_nit"]
        )


def downgrade() -> None:
    if _column_exists("ingest_jobs", "company_nit"):
        op.drop_index("ix_ingest_jobs_company_nit", table_name="ingest_jobs")
        op.drop_column("ingest_jobs", "company_nit")
