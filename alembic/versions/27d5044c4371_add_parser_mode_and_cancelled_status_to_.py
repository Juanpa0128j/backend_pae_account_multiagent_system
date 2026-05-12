"""add parser_mode and cancelled status to ingest_jobs

Revision ID: 27d5044c4371
Revises: k9l0m1n2o3p4
Create Date: 2026-05-11 20:37:43.115860

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "27d5044c4371"
down_revision: Union[str, Sequence[str], None] = "k9l0m1n2o3p4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE ingeststatus ADD VALUE IF NOT EXISTS 'CANCELLED'")
    op.add_column(
        "ingest_jobs",
        sa.Column(
            "parser_mode",
            sa.String(length=20),
            server_default="fast",
            nullable=False,
            comment="LlamaParse extraction mode: fast|standard|premium|gpt4o",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("ingest_jobs", "parser_mode")
    # NOTE: PostgreSQL does not support removing enum values directly.
    # To fully revert, recreate the enum without 'cancelled' and swap columns.
    # Skipped here because 'cancelled' is harmless if unused.
