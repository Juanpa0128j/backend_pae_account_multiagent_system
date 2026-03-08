"""add_vector_documents

Revision ID: c3f8a2d91b5e
Revises: 8fb1b0855393
Create Date: 2026-03-08 00:00:00.000000

Adds the `vector_documents` table that replaces ChromaDB as the vector store.
Uses the pgvector extension (already bundled with Supabase).

Table design:
  - id TEXT PRIMARY KEY   — human-readable IDs (e.g. "puc_1105") or UUID strings
  - collection_name       — virtual "collection" partition (was ChromaDB collection)
  - content TEXT          — the stored document text
  - embedding vector(1024)— BAAI/bge-m3 dense embeddings (1024 dims)
  - metadata JSONB        — arbitrary key-value metadata
  - created_at            — used for `get_recent()` ordering

Indexes:
  - HNSW index on embedding for fast cosine similarity search
  - B-tree index on collection_name for efficient collection filtering
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c3f8a2d91b5e"
down_revision: Union[str, Sequence[str], None] = "8fb1b0855393"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension (no-op if already enabled — safe on Supabase)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create the unified vector documents table
    op.execute("""
        CREATE TABLE IF NOT EXISTS vector_documents (
            id              TEXT PRIMARY KEY,
            collection_name VARCHAR(255) NOT NULL,
            content         TEXT NOT NULL,
            embedding       vector(1024),
            metadata        JSONB DEFAULT '{}',
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # B-tree index for fast collection filtering (WHERE collection_name = ...)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_collection
        ON vector_documents (collection_name)
    """)

    # HNSW index for approximate nearest-neighbour cosine search
    # m=16 and ef_construction=64 are sensible defaults for this dataset size
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_embedding_hnsw
        ON vector_documents
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_collection")
    op.execute("DROP TABLE IF EXISTS vector_documents")
