"""add tax_concepts table + concepto_retencion / tipo_persona_emisor on transactions_posted

Implements F350 discrimination per Res. DIAN 000031/2024:
  * tax_concepts catalog (renglón, aplica_a PJ/PN/AMB, tarifa_default, base UVT)
  * transactions_posted.concepto_retencion (logical FK to tax_concepts.code)
  * transactions_posted.tipo_persona_emisor (PJ | PN)

Idempotent: each ALTER / CREATE checked against information_schema.

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-05-24 19:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "y5z6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "x4y5z6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TIPO_PERSONA_CHECK = "transactions_posted_tipo_persona_emisor_check"
_APLICA_A_CHECK = "tax_concepts_aplica_a_check"


def _table_exists(bind, name: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (name,),
        ).first()
        is not None
    )


def _column_exists(bind, table: str, column: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
            (table, column),
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

    # ── 1. Create tax_concepts catalog ──────────────────────────────────────
    if not _table_exists(bind, "tax_concepts"):
        op.execute("""
            CREATE TABLE tax_concepts (
                code VARCHAR(16) PRIMARY KEY,
                label VARCHAR(255) NOT NULL,
                renglon_350 VARCHAR(8) NOT NULL,
                aplica_a VARCHAR(4) NOT NULL,
                tarifa_default NUMERIC(6,4) NULL,
                base_minima_uvt NUMERIC(8,2) NULL,
                categoria VARCHAR(32) NOT NULL,
                art_referencia VARCHAR(64) NULL,
                activo BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """)

    if not _constraint_exists(bind, _APLICA_A_CHECK):
        op.execute(
            f"ALTER TABLE tax_concepts "
            f"ADD CONSTRAINT {_APLICA_A_CHECK} "
            f"CHECK (aplica_a IN ('PJ','PN','AMB'))"
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tax_concepts_categoria "
        "ON tax_concepts (categoria)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tax_concepts_renglon_350 "
        "ON tax_concepts (renglon_350)"
    )

    # ── 2. transactions_posted.concepto_retencion ───────────────────────────
    if not _column_exists(bind, "transactions_posted", "concepto_retencion"):
        op.execute(
            "ALTER TABLE transactions_posted "
            "ADD COLUMN concepto_retencion VARCHAR(16) NULL"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_transactions_posted_concepto_retencion "
        "ON transactions_posted (concepto_retencion)"
    )

    # ── 3. transactions_posted.tipo_persona_emisor ──────────────────────────
    if not _column_exists(bind, "transactions_posted", "tipo_persona_emisor"):
        op.execute(
            "ALTER TABLE transactions_posted "
            "ADD COLUMN tipo_persona_emisor VARCHAR(2) NULL"
        )
    if not _constraint_exists(bind, _TIPO_PERSONA_CHECK):
        op.execute(
            f"ALTER TABLE transactions_posted "
            f"ADD CONSTRAINT {_TIPO_PERSONA_CHECK} "
            f"CHECK (tipo_persona_emisor IS NULL OR tipo_persona_emisor IN ('PJ','PN'))"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_transactions_posted_tipo_persona_emisor "
        "ON transactions_posted (tipo_persona_emisor)"
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.execute("DROP INDEX IF EXISTS ix_transactions_posted_tipo_persona_emisor")
    if _constraint_exists(bind, _TIPO_PERSONA_CHECK):
        op.execute(
            f"ALTER TABLE transactions_posted DROP CONSTRAINT {_TIPO_PERSONA_CHECK}"
        )
    if _column_exists(bind, "transactions_posted", "tipo_persona_emisor"):
        op.execute("ALTER TABLE transactions_posted DROP COLUMN tipo_persona_emisor")

    op.execute("DROP INDEX IF EXISTS ix_transactions_posted_concepto_retencion")
    if _column_exists(bind, "transactions_posted", "concepto_retencion"):
        op.execute("ALTER TABLE transactions_posted DROP COLUMN concepto_retencion")

    op.execute("DROP INDEX IF EXISTS ix_tax_concepts_renglon_350")
    op.execute("DROP INDEX IF EXISTS ix_tax_concepts_categoria")
    if _constraint_exists(bind, _APLICA_A_CHECK):
        op.execute(f"ALTER TABLE tax_concepts DROP CONSTRAINT {_APLICA_A_CHECK}")
    if _table_exists(bind, "tax_concepts"):
        op.execute("DROP TABLE tax_concepts")
