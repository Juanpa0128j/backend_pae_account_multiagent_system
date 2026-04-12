"""add chat_sessions and chat_messages tables

Revision ID: 5bc6243a55b2
Revises: f3a4b5c6d7e8
Create Date: 2026-04-04 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "5bc6243a55b2"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :name AND table_schema = 'public'"
        ),
        {"name": name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Create chat_sessions and chat_messages tables — idempotent."""
    if not _table_exists("chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.String(50), primary_key=True),
            sa.Column("company_nit", sa.String(20), nullable=True),
            sa.Column("title", sa.String(200), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
        )
        op.create_index(
            "ix_chat_sessions_company_nit", "chat_sessions", ["company_nit"]
        )
        op.create_index("ix_chat_sessions_created_at", "chat_sessions", ["created_at"])

    if not _table_exists("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.String(50), primary_key=True),
            sa.Column(
                "session_id",
                sa.String(50),
                sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(10), nullable=False),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("data_cards", JSONB, nullable=True),
            sa.Column("intent", sa.String(30), nullable=True),
            sa.Column("sources", JSONB, nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
        )
        op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
        op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])


def downgrade() -> None:
    """Drop chat tables."""
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
