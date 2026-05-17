"""add cuenta_ica_propio to company_settings

Revision ID: l0m1n2o3p4q5
Revises: k9l0m1n2o3p4
Create Date: 2026-05-13 10:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l0m1n2o3p4q5"
down_revision: Union[str, Sequence[str], None] = "k9l0m1n2o3p4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("company_settings")}
    if "cuenta_ica_propio" in existing:
        return
    op.add_column(
        "company_settings",
        sa.Column(
            "cuenta_ica_propio",
            sa.String(10),
            nullable=True,
            server_default="2368",
            comment="PUC account for ICA liability (ReteICA por pagar). Default 2368; override if company uses a different account.",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("company_settings")}
    if "cuenta_ica_propio" not in existing:
        return
    op.drop_column("company_settings", "cuenta_ica_propio")
