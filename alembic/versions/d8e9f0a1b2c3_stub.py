"""stub: recover missing revision d8e9f0a1b2c3

This migration was applied to the CI/production database but the original
file was lost. This stub re-introduces the revision ID into the chain so
that `alembic upgrade head` can proceed from this point.

Revision ID: d8e9f0a1b2c3
Revises: 5bc6243a55b2
Create Date: 2026-04-26

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "5bc6243a55b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: schema changes from the original migration are already
    # present in the database. This stub only restores
    # the revision record so Alembic can continue upgrading.
    pass


def downgrade() -> None:
    pass
