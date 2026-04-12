"""stub: recover missing revision a1b2c3d4e5f6

This migration was applied to the production database but the original
file was lost. This stub re-introduces the revision ID into the chain so
that `alembic upgrade head` can proceed from this point.

Revision ID: a1b2c3d4e5f6
Revises: 8fb1b0855393
Create Date: 2026-04-11

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "8fb1b0855393"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: schema changes from the original migration are already
    # present in the production database. This stub only restores
    # the revision record so Alembic can continue upgrading.
    pass


def downgrade() -> None:
    pass
