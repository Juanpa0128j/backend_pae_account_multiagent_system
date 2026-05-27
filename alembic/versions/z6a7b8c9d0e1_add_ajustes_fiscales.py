"""add ajustes_fiscales table for F2516 fiscal reconciliation adjustments

Stores per-NIT/year fiscal adjustments (contable vs fiscal) by section/concept,
enabling auto-population of the F2516 Conciliación Fiscal form (Art. 772-1 ET,
Res. DIAN 000049/2019, formato F2516v9).

Idempotent: each ALTER / CREATE checked against information_schema.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-05-24 21:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "z6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "y5z6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SECCION_CHECK = "ajustes_fiscales_seccion_check"
_TIPO_DIF_CHECK = "ajustes_fiscales_tipo_diferencia_check"
_UNIQUE_CONSTRAINT = "ajustes_fiscales_nit_year_seccion_concepto_key"


def _table_exists(bind, name: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (name,),
        ).first()
        is not None
    )


def _constraint_exists(bind, name: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_schema='public' AND constraint_name=%s",
            (name,),
        ).first()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "ajustes_fiscales"):
        op.execute("""
            CREATE TABLE ajustes_fiscales (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                company_nit VARCHAR(20) NOT NULL
                    REFERENCES company_settings(nit) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                seccion VARCHAR(32) NOT NULL,
                concepto VARCHAR(64) NOT NULL,
                valor_contable NUMERIC(18,2) NOT NULL DEFAULT 0,
                valor_fiscal NUMERIC(18,2) NOT NULL DEFAULT 0,
                tipo_diferencia VARCHAR(32) NOT NULL,
                descripcion TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """)

    if not _constraint_exists(bind, _SECCION_CHECK):
        op.execute(
            f"ALTER TABLE ajustes_fiscales "
            f"ADD CONSTRAINT {_SECCION_CHECK} "
            f"CHECK (seccion IN ('ESF_ACTIVO','ESF_PASIVO','ESF_PATRIMONIO',"
            f"'ERI_INGRESO','ERI_COSTO','ERI_GASTO'))"
        )

    if not _constraint_exists(bind, _TIPO_DIF_CHECK):
        op.execute(
            f"ALTER TABLE ajustes_fiscales "
            f"ADD CONSTRAINT {_TIPO_DIF_CHECK} "
            f"CHECK (tipo_diferencia IN ('permanente','temporaria_imponible',"
            f"'temporaria_deducible'))"
        )

    if not _constraint_exists(bind, _UNIQUE_CONSTRAINT):
        op.execute(
            f"ALTER TABLE ajustes_fiscales "
            f"ADD CONSTRAINT {_UNIQUE_CONSTRAINT} "
            f"UNIQUE (company_nit, year, seccion, concepto)"
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ajustes_fiscales_nit_year "
        "ON ajustes_fiscales (company_nit, year)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ajustes_fiscales_seccion "
        "ON ajustes_fiscales (seccion)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_ajustes_fiscales_seccion")
    op.execute("DROP INDEX IF EXISTS ix_ajustes_fiscales_nit_year")
    if _constraint_exists(bind, _UNIQUE_CONSTRAINT):
        op.execute(f"ALTER TABLE ajustes_fiscales DROP CONSTRAINT {_UNIQUE_CONSTRAINT}")
    if _constraint_exists(bind, _TIPO_DIF_CHECK):
        op.execute(f"ALTER TABLE ajustes_fiscales DROP CONSTRAINT {_TIPO_DIF_CHECK}")
    if _constraint_exists(bind, _SECCION_CHECK):
        op.execute(f"ALTER TABLE ajustes_fiscales DROP CONSTRAINT {_SECCION_CHECK}")
    if _table_exists(bind, "ajustes_fiscales"):
        op.execute("DROP TABLE ajustes_fiscales")
