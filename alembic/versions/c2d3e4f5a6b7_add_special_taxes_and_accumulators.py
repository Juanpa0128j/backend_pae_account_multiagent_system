"""add special_taxes and special_tax_accumulators tables

Adds per-company configurable special withholding taxes (estampilla, timbre, etc.)
and a periodic accumulator table for settlement-deferred taxes.

Revision ID: a1b2c3d4e5f6
Revises: z6a7b8c9d0e1
Create Date: 2026-06-13 14:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, name: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (name,),
        ).first()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "special_taxes"):
        op.create_table(
            "special_taxes",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("company_nit", sa.String(20), nullable=False),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("nombre", sa.String(255), nullable=False),
            sa.Column("descripcion", sa.Text, nullable=True),
            sa.Column("rate", sa.Numeric(10, 6), nullable=False),
            sa.Column("base_calc", sa.String(20), nullable=False),
            sa.Column("base_calc_formula", sa.Text, nullable=True),
            sa.Column(
                "applies_to_doc_types",
                postgresql.ARRAY(sa.String),
                nullable=False,
                server_default="{}",
            ),
            sa.Column(
                "es_entidad_publica_only",
                sa.Boolean,
                nullable=False,
                server_default="false",
            ),
            sa.Column(
                "settlement",
                sa.String(20),
                nullable=False,
                server_default="per_transaction",
            ),
            sa.Column("cuenta_gasto", sa.String(10), nullable=False),
            sa.Column("cuenta_por_pagar", sa.String(10), nullable=False),
            sa.Column("norma_referencia", sa.String(255), nullable=True),
            sa.Column("vigente_desde", sa.Date, nullable=True),
            sa.Column("vigente_hasta", sa.Date, nullable=True),
            sa.Column("activo", sa.Boolean, nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint(
                "company_nit", "code", name="uq_special_taxes_nit_code"
            ),
        )
        op.create_index(
            "ix_special_taxes_company_nit", "special_taxes", ["company_nit"]
        )

    if not _table_exists(bind, "special_tax_accumulators"):
        op.create_table(
            "special_tax_accumulators",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "special_tax_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("special_taxes.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("company_nit", sa.String(20), nullable=False),
            sa.Column("period_year", sa.Integer, nullable=False),
            sa.Column("period_month", sa.Integer, nullable=False),
            sa.Column(
                "accumulated_base",
                sa.Numeric(18, 2),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "accumulated_tax",
                sa.Numeric(18, 2),
                nullable=False,
                server_default="0",
            ),
            sa.Column("liquidated", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("liquidated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint(
                "special_tax_id",
                "period_year",
                "period_month",
                name="uq_special_tax_accumulators_tax_period",
            ),
        )
        op.create_index(
            "ix_special_tax_accumulators_special_tax_id",
            "special_tax_accumulators",
            ["special_tax_id"],
        )
        op.create_index(
            "ix_special_tax_accumulators_company_nit",
            "special_tax_accumulators",
            ["company_nit"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "special_tax_accumulators"):
        op.drop_table("special_tax_accumulators")
    if _table_exists(bind, "special_taxes"):
        op.drop_table("special_taxes")
