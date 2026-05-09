"""stub: recover missing revision i7j8k9l0m1n2

Another revision id found in the production alembic_version table whose
migration file isn't merged into this branch. Stubbed so `alembic upgrade
head` can advance from the DB state to our locked_pathway migration.

Revision ID: i7j8k9l0m1n2
Revises: fb08f11836cb
Create Date: 2026-05-09

"""

from typing import Sequence, Union

revision: str = "i7j8k9l0m1n2"
down_revision: Union[str, Sequence[str], None] = "fb08f11836cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
