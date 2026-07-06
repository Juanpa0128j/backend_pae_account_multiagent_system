"""add_embedding_to_vector_documents

`8fb1b0855393_initial_schema` creates vector_documents with
`CREATE TABLE IF NOT EXISTS`. On any DB where the table predates that
squash (e.g. production, created by the original pre-squash migrations),
`IF NOT EXISTS` makes the whole block a no-op, so `embedding` was never
added there. RAG hybrid search then fails with
`psycopg2.errors.UndefinedColumn: column "embedding" does not exist`.

Same idempotent check-then-ALTER pattern as q5r6s7t8u9v0 (content_tsv).

Revision ID: f1e2d3c4b5a6
Revises: a3c4d5e6f7b8
Create Date: 2026-07-05 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, Sequence[str], None] = "a3c4d5e6f7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    exists = bind.exec_driver_sql("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vector_documents' AND column_name = 'embedding'
        """).first()

    if not exists:
        op.execute("ALTER TABLE vector_documents ADD COLUMN embedding vector(1024)")

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vector_documents_embedding_hnsw
            ON vector_documents USING hnsw (embedding vector_cosine_ops)
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vector_documents_embedding_hnsw")
    op.execute("ALTER TABLE vector_documents DROP COLUMN IF EXISTS embedding")
