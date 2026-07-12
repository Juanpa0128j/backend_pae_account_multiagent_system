"""add parse_cache table for shared LlamaParse results

Revision ID: h3i4j5k6l7m8
Revises: g2f3e4d5c6b7
Create Date: 2026-07-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h3i4j5k6l7m8"
down_revision: Union[str, Sequence[str], None] = "g2f3e4d5c6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parse_cache",
        sa.Column("content_sha256", sa.String(length=64), primary_key=True),
        sa.Column("parser_mode", sa.String(length=20), primary_key=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_parse_cache_created_at", "parse_cache", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_parse_cache_created_at", table_name="parse_cache")
    op.drop_table("parse_cache")
