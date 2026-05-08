"""stub: recover missing revision h6i7j8k9l0m1

This migration was applied to the database but the original file was lost.
This stub re-introduces the revision ID into the chain so that
`alembic upgrade head` can proceed.

Revision ID: h6i7j8k9l0m1
Revises: g5h6i7j8k9l0
Create Date: 2026-05-03 00:00:01.000000

"""

from typing import Sequence, Union

revision: str = "h6i7j8k9l0m1"
down_revision: Union[str, Sequence[str], None] = "g5h6i7j8k9l0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
