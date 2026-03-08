"""add_fts_column

Revision ID: d4e5f6a7b8c9
Revises: c3f8a2d91b5e
Create Date: 2026-03-15 00:00:00.000000

Adds a generated tsvector column and GIN index to `vector_documents` for
full-text search in Spanish, enabling hybrid BM25+vector retrieval with
Reciprocal Rank Fusion (RRF) scoring.

Changes:
  - ALTER TABLE vector_documents
    ADD COLUMN content_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED

  - CREATE INDEX ix_vector_documents_content_tsv
    ON vector_documents USING gin(content_tsv)

The GENERATED ALWAYS AS ... STORED syntax is available from PostgreSQL 12+
(Supabase runs PostgreSQL 15+). The column is automatically re-computed on
INSERT/UPDATE, so no backfill script is needed -- existing rows are updated
automatically when the ALTER TABLE statement completes.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3f8a2d91b5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated tsvector column for Spanish full-text search.
    # GENERATED ALWAYS AS ... STORED is auto-maintained by PostgreSQL.
    op.execute("""
        ALTER TABLE vector_documents
        ADD COLUMN IF NOT EXISTS content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED
    """)

    # GIN index for fast @@ (tsvector match) full-text queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_content_tsv
        ON vector_documents USING gin(content_tsv)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_content_tsv")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS content_tsv")
