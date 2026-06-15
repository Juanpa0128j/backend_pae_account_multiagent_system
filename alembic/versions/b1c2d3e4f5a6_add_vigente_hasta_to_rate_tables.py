"""add vigente_hasta to national_rates and company_rate_overrides

Adds a nullable Date end-date column (vigente_hasta) to both rate tables,
mirroring the effective_from/effective_to pattern already used by
TaxBaseMinima and UvtValue.

national_rates.vigente_hasta         — open-ended end date for statutory rates
company_rate_overrides.vigente_hasta — open-ended end date for company overrides

Idempotent: each ALTER checked against information_schema.

Revision ID: b1c2d3e4f5a6
Revises: daba0a72d36b
Create Date: 2026-06-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "daba0a72d36b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    bind = op.get_bind()

    if not _column_exists(bind, "national_rates", "vigente_hasta"):
        op.add_column(
            "national_rates",
            sa.Column(
                "vigente_hasta",
                sa.Date(),
                nullable=True,
                comment="End date of this rate (null = open-ended)",
            ),
        )

    if not _column_exists(bind, "company_rate_overrides", "vigente_hasta"):
        op.add_column(
            "company_rate_overrides",
            sa.Column(
                "vigente_hasta",
                sa.Date(),
                nullable=True,
                comment="End date of this override (null = open-ended)",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _column_exists(bind, "company_rate_overrides", "vigente_hasta"):
        op.drop_column("company_rate_overrides", "vigente_hasta")

    if _column_exists(bind, "national_rates", "vigente_hasta"):
        op.drop_column("national_rates", "vigente_hasta")
