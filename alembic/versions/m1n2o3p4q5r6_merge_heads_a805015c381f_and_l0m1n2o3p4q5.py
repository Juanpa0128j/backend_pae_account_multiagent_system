"""merge heads a805015c381f and l0m1n2o3p4q5

Revision ID: m1n2o3p4q5r6
Revises: a805015c381f, l0m1n2o3p4q5
Create Date: 2026-05-13 11:30:00.000000

"""

from typing import Sequence, Union

revision: str = "m1n2o3p4q5r6"
down_revision: Union[str, Sequence[str], None] = ("a805015c381f", "l0m1n2o3p4q5")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
