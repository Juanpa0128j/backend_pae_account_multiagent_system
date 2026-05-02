"""backfill vector_documents fts + embedding columns and indexes

Revision ID: f1b2c3d4e5f6
Revises: e0a1b2c3d4e5
Create Date: 2026-04-27

Repairs prod databases where vector_documents was created from an old
schema and never picked up the embedding + content_tsv columns or their
indexes.

Root cause: 8fb1b0855393_initial_schema used CREATE TABLE IF NOT EXISTS
to absorb earlier separate migrations (c3f8a2d91b5e for embedding,
d4e5f6a7b8c9 for content_tsv). When the table already existed without
those columns, the IF NOT EXISTS skipped the create and never added
the missing columns. RAG hybrid search then fails with
``column "content_tsv" does not exist`` and silently degrades.

This migration is idempotent: every step uses IF NOT EXISTS so it's
safe to run on healthy DBs (no-op) and on broken DBs (repairs).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f1b2c3d4e5f6"
down_revision = "e0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # extension safety net (no-op if already installed)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # columns
    op.execute("""
        ALTER TABLE vector_documents
        ADD COLUMN IF NOT EXISTS embedding vector(1024)
        """)
    op.execute("""
        ALTER TABLE vector_documents
        ADD COLUMN IF NOT EXISTS content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED
        """)

    # indexes
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_collection_name
            ON vector_documents (collection_name)
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_content_tsv_gin
            ON vector_documents USING GIN (content_tsv)
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_embedding_hnsw
            ON vector_documents USING hnsw (embedding vector_cosine_ops)
        """)


def downgrade() -> None:
    # Indexes drop first to avoid HNSW dependency on column.
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_content_tsv_gin")
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_collection_name")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS content_tsv")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS embedding")
