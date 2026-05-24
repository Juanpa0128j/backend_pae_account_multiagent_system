"""add_content_tsv_to_vector_documents

The hybrid RRF search in `vectordb.search_hybrid()` references a `content_tsv`
tsvector column on `vector_documents`. Production DB is missing the column,
causing every RAG lookup to fall back to vec-only and emit a warning. This
migration adds the column as a STORED generated column over `content`, plus
a GIN index for ts_rank_cd lookups.

Idempotent: checks existence before adding.

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-05-23 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "q5r6s7t8u9v0"
down_revision: Union[str, Sequence[str], None] = "p4q5r6s7t8u9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.exec_driver_sql("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vector_documents' AND column_name = 'content_tsv'
        """).first()

    if not exists:
        op.execute("""
            ALTER TABLE vector_documents
            ADD COLUMN content_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('spanish', coalesce(content, ''))) STORED
            """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS vector_documents_content_tsv_idx
        ON vector_documents USING GIN (content_tsv)
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS vector_documents_content_tsv_idx")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS content_tsv")
