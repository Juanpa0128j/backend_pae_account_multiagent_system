"""add national_rates table for configurable statutory tax rates

Stores the Colombian national statutory tax rates currently hardcoded in
settings.py (_TASA_RETEFUENTE_SERVICIOS=0.04, _TASA_RETEFUENTE_BIENES=0.025,
_TASA_RETEFUENTE_ARRENDAMIENTO=0.035, tasa_renta=0.35). Seeded with 2026
statutory values + legal references per ET.

Allows the /setup endpoint to read rates from DB instead of module-level
constants, enabling rate updates without a code deploy.

Idempotent: CREATE and INSERT checked against information_schema / ON CONFLICT.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
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

    if not _table_exists(bind, "national_rates"):
        op.execute("""
            CREATE TABLE national_rates (
                code VARCHAR(64) PRIMARY KEY,
                value NUMERIC(8,6) NOT NULL,
                descripcion VARCHAR(255) NOT NULL,
                norma_referencia VARCHAR(128) NOT NULL,
                vigente_desde DATE NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

    # Seed with 2026 statutory values — ON CONFLICT DO NOTHING for idempotency
    op.execute("""
        INSERT INTO national_rates (code, value, descripcion, norma_referencia, vigente_desde)
        VALUES
            ('retefuente_servicios',     0.04,   'Retención en la fuente — servicios generales',     'Art. 392 ET',              '2023-01-01'),
            ('retefuente_bienes',        0.025,  'Retención en la fuente — compra de bienes',        'Art. 401 ET',              '2023-01-01'),
            ('retefuente_arrendamiento', 0.035,  'Retención en la fuente — arrendamiento inmuebles', 'Art. 401 ET',              '2023-01-01'),
            ('renta_general',            0.35,   'Tarifa general impuesto sobre la renta',           'Art. 240 ET, L.2277/2022', '2023-01-01')
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "national_rates"):
        op.execute("DROP TABLE national_rates")
