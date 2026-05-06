"""stub: recover missing revision f2c3d4e5f6a7

This migration was applied to the local/CI database but the original file
was lost. This stub re-introduces the revision ID into the chain so that
`alembic upgrade head` can proceed from this point.

Revision ID: f2c3d4e5f6a7
Revises: e0a1b2c3d4e5
Create Date: 2026-05-02 10:00:00.000000

"""

from typing import Sequence, Union

revision: str = "f2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "e0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
