"""align vector_documents content_tsv to 'spanish' tsvector config

Revision ID: b1c2d3e4f5a6
Revises: f2c3d4e5f6a7
Create Date: 2026-05-03

Aligns the GENERATED ``content_tsv`` column with the query side of
``app/core/vectordb.py::search_hybrid``, which uses
``plainto_tsquery('spanish', ...)``. Previously the column was created with
``to_tsvector('simple', ...)`` (both in the original ``8fb1b0855393`` schema
and in the recent backfill ``f1b2c3d4e5f6``), so Spanish stemming applied at
query time produced near-zero matches against raw-token storage. The vector
half of hybrid search (HNSW) was carrying all retrieval weight; FTS path was
silently degraded.

Cannot ALTER the config of a GENERATED ALWAYS column. We DROP + recreate the
column with the new config; rows recompute automatically since ``content`` is
intact. The HNSW vector index and ``embedding`` column are untouched.

Idempotent: re-running this migration after it has already been applied is a
no-op semantically (drops + recreates the same definition).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "f2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop GIN index first (depends on the column) and the column itself.
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_content_tsv_gin")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS content_tsv")

    # Recreate with 'spanish' config so stemming matches plainto_tsquery('spanish', ...).
    op.execute("""
        ALTER TABLE vector_documents
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('spanish', coalesce(content, ''))) STORED
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_content_tsv_gin
            ON vector_documents USING GIN (content_tsv)
        """)


def downgrade() -> None:
    # Reverse: restore 'simple' config (state prior to this migration).
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_content_tsv_gin")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS content_tsv")
    op.execute("""
        ALTER TABLE vector_documents
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_content_tsv_gin
            ON vector_documents USING GIN (content_tsv)
        """)
