"""add_tarifas_renta — regulatory income-tax rate table + company_settings regime columns

Creates tarifas_renta table and adds regimen_tributario / actividad_economica
columns to company_settings. All DDL is idempotent via information_schema checks.

Revision ID: u1v2w3x4y5z6
Revises: t8u9v0w1x2y3
Create Date: 2026-05-24 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, None] = "t8u9v0w1x2y3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Create tarifas_renta table (idempotent) ─────────────────────────
    table_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'tarifas_renta'"
        )
    ).fetchone()

    if not table_exists:
        op.create_table(
            "tarifas_renta",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "regimen",
                sa.String(32),
                nullable=False,
                comment="ordinario | esal | zona_franca | rst",
            ),
            sa.Column(
                "actividad",
                sa.String(32),
                nullable=True,
                comment="general | financiero | hidroelectrico | otro — NULL means any",
            ),
            sa.Column(
                "tarifa_base",
                sa.Numeric(5, 4),
                nullable=False,
                comment="Base rate as decimal fraction, e.g. 0.3500",
            ),
            sa.Column(
                "sobretasa",
                sa.Numeric(5, 4),
                nullable=False,
                server_default="0",
                comment="Surcharge decimal fraction, e.g. 0.0500",
            ),
            sa.Column(
                "year_from",
                sa.Integer(),
                nullable=False,
                comment="First tax year this row applies",
            ),
            sa.Column(
                "year_to",
                sa.Integer(),
                nullable=True,
                comment="Last tax year (inclusive); NULL = open-ended / currently valid",
            ),
            sa.Column(
                "base_legal",
                sa.String(128),
                nullable=True,
                comment="Legal authority, e.g. 'Art. 240 ET (Ley 2277/2022)'",
            ),
            sa.Column("notas", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "regimen", "actividad", "year_from", name="uq_tarifas_renta_key"
            ),
        )
        op.create_index(
            "idx_tarifas_renta_year",
            "tarifas_renta",
            ["year_from", "year_to"],
        )

    # ── 2. Add regimen_tributario to company_settings (idempotent) ──────────
    col_regimen_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'company_settings' "
            "AND column_name = 'regimen_tributario'"
        )
    ).fetchone()

    if not col_regimen_exists:
        op.add_column(
            "company_settings",
            sa.Column(
                "regimen_tributario",
                sa.String(32),
                nullable=False,
                server_default="ordinario",
                comment="Tax regime: ordinario | esal | zona_franca | rst",
            ),
        )

    # ── 3. Add actividad_economica to company_settings (idempotent) ─────────
    col_actividad_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'company_settings' "
            "AND column_name = 'actividad_economica'"
        )
    ).fetchone()

    if not col_actividad_exists:
        op.add_column(
            "company_settings",
            sa.Column(
                "actividad_economica",
                sa.String(32),
                nullable=False,
                server_default="general",
                comment="Economic activity type: general | financiero | hidroelectrico | otro",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Drop new columns from company_settings
    for col in ("actividad_economica", "regimen_tributario"):
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                f"WHERE table_name = 'company_settings' AND column_name = '{col}'"
            )
        ).fetchone()
        if exists:
            op.drop_column("company_settings", col)

    # Drop tarifas_renta
    table_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'tarifas_renta'"
        )
    ).fetchone()
    if table_exists:
        op.drop_index("idx_tarifas_renta_year", table_name="tarifas_renta")
        op.drop_table("tarifas_renta")
