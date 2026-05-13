"""fix ingeststatus cancelled enum casing

Revision ID: a805015c381f
Revises: 27d5044c4371
Create Date: 2026-05-11 23:01:44.083425

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a805015c381f"
down_revision: Union[str, Sequence[str], None] = "27d5044c4371"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add CANCELLED value to ingeststatus enum with correct casing."""
    op.execute("ALTER TYPE ingeststatus ADD VALUE IF NOT EXISTS 'CANCELLED'")


def downgrade() -> None:
    """Downgrade schema.

    NOTE: PostgreSQL does not support removing enum values directly.
    To fully revert, recreate the enum without 'CANCELLED' and swap columns.
    Skipped here because 'CANCELLED' is harmless if unused.
    """
    pass
