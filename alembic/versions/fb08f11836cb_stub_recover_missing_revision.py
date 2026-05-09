"""stub: recover missing revision fb08f11836cb

This revision ID was found in the production alembic_version table but the
corresponding migration file was never merged into this branch. The stub
re-introduces the revision into the chain (between 33c6ac057344 and the
locked_pathway migration) so `alembic upgrade head` can proceed without the
"Can't locate revision" error.

Revision ID: fb08f11836cb
Revises: 33c6ac057344
Create Date: 2026-05-09

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "fb08f11836cb"
down_revision: Union[str, Sequence[str], None] = "33c6ac057344"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: whatever schema changes were applied under this revision id
    # are already in the production database. This stub only restores the
    # revision record so Alembic can continue upgrading.
    pass


def downgrade() -> None:
    pass
