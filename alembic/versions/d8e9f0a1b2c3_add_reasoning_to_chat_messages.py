"""add reasoning to chat_messages

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-04-26 04:00:00.000000

Adds a JSONB column to persist the agent's step-by-step reasoning trace
(intent classification, parameters, data gathering, RAG, generation) so
that loaded chat sessions reproduce the inline reasoning panel.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists("chat_messages", "reasoning"):
        op.add_column(
            "chat_messages",
            sa.Column("reasoning", JSONB, nullable=True),
        )


def downgrade() -> None:
    if _column_exists("chat_messages", "reasoning"):
        op.drop_column("chat_messages", "reasoning")
