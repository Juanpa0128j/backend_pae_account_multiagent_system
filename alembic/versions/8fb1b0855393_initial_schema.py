"""initial_schema

Includes: all tables created at project start + company_settings (added 2026-03-08)

Revision ID: 8fb1b0855393
Revises:
Create Date: 2026-02-28 18:29:24.217871

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8fb1b0855393'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum types — use Python enum .name (uppercase) because SQLAlchemy sends .name by default
tercero_tipo = sa.Enum('PROVEEDOR', 'CLIENTE', 'AMBOS', name='tercerotipo')
naturaleza_cuenta = sa.Enum('DEBITO', 'CREDITO', name='naturalezacuenta')
ingest_status = sa.Enum('PENDING_PROCESSING', 'PROCESSING', 'COMPLETED', 'FAILED', name='ingeststatus')
transaction_status = sa.Enum('PENDING', 'PROCESSING', 'POSTED', 'REJECTED', 'ERROR', name='transactionstatus')
process_status = sa.Enum('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', name='processstatus')


def upgrade() -> None:
    """Create all tables for the PAE accounting system."""
    # Enum types are created automatically by sa.Enum in create_table

    # ── terceros ──
    op.create_table(
        'terceros',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('nit', sa.String(20), nullable=False),
        sa.Column('razon_social', sa.String(255), nullable=False),
        sa.Column('tipo', tercero_tipo, nullable=True),
        sa.Column('actividad_economica', sa.String(10), nullable=True),
        sa.Column('direccion', sa.String(255), nullable=True),
        sa.Column('telefono', sa.String(20), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nit'),
    )
    op.create_index('ix_terceros_nit', 'terceros', ['nit'])

    # ── cuentas_puc ──
    op.create_table(
        'cuentas_puc',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('codigo', sa.String(10), nullable=False),
        sa.Column('nombre', sa.String(255), nullable=False),
        sa.Column('clase', sa.Integer(), nullable=False, comment='1=Activo,2=Pasivo,3=Patrimonio,4=Ingreso,5=Gasto,6=Costo'),
        sa.Column('grupo', sa.String(4), nullable=True),
        sa.Column('cuenta', sa.String(6), nullable=True),
        sa.Column('subcuenta', sa.String(8), nullable=True),
        sa.Column('naturaleza', naturaleza_cuenta, nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=True),
        sa.Column('activa', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('codigo'),
    )
    op.create_index('ix_cuentas_puc_codigo', 'cuentas_puc', ['codigo'])

    # ── ingest_jobs ──
    op.create_table(
        'ingest_jobs',
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_path', sa.String(500), nullable=True),
        sa.Column('status', ingest_status, nullable=False, server_default='PENDING_PROCESSING'),
        sa.Column('raw_preview', postgresql.JSONB(), nullable=True),
        sa.Column('extraction_errors', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ingest_jobs_id', 'ingest_jobs', ['id'])

    # ── transactions_pending ──
    op.create_table(
        'transactions_pending',
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('ingest_id', sa.String(50), nullable=False),
        sa.Column('fecha', sa.DateTime(timezone=True), nullable=True),
        sa.Column('nit_emisor', sa.String(20), nullable=True),
        sa.Column('nit_receptor', sa.String(20), nullable=True),
        sa.Column('total', sa.Numeric(15, 2), nullable=True),
        sa.Column('descripcion', sa.Text(), nullable=True),
        sa.Column('items', postgresql.JSONB(), nullable=True),
        sa.Column('raw_data', postgresql.JSONB(), nullable=True),
        sa.Column('status', transaction_status, nullable=False, server_default='PENDING'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['ingest_id'], ['ingest_jobs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_transactions_pending_id', 'transactions_pending', ['id'])
    op.create_index('ix_transactions_pending_ingest_id', 'transactions_pending', ['ingest_id'])
    op.create_index('ix_transactions_pending_nit_emisor', 'transactions_pending', ['nit_emisor'])
    op.create_index('ix_transactions_pending_nit_receptor', 'transactions_pending', ['nit_receptor'])

    # ── transactions_posted ──
    op.create_table(
        'transactions_posted',
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('transaction_pending_id', sa.String(50), nullable=False),
        sa.Column('cuenta_puc', sa.String(10), nullable=False),
        sa.Column('puc_descripcion', sa.String(255), nullable=True),
        sa.Column('retefuente', sa.Numeric(15, 2), server_default='0'),
        sa.Column('reteica', sa.Numeric(15, 2), server_default='0'),
        sa.Column('iva', sa.Numeric(15, 2), server_default='0'),
        sa.Column('neto_a_pagar', sa.Numeric(15, 2), server_default='0'),
        sa.Column('journal_entries_json', postgresql.JSONB(), nullable=True),
        sa.Column('tax_references', postgresql.JSONB(), nullable=True),
        sa.Column('agent_reasoning', postgresql.JSONB(), nullable=True),
        sa.Column('status', transaction_status, nullable=False, server_default='POSTED'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['transaction_pending_id'], ['transactions_pending.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_transactions_posted_id', 'transactions_posted', ['id'])
    op.create_index('ix_transactions_posted_pending_id', 'transactions_posted', ['transaction_pending_id'])
    op.create_index('ix_transactions_posted_cuenta_puc', 'transactions_posted', ['cuenta_puc'])

    # ── journal_entry_lines ──
    op.create_table(
        'journal_entry_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('transaction_posted_id', sa.String(50), nullable=False),
        sa.Column('fecha', sa.DateTime(timezone=True), nullable=False),
        sa.Column('comprobante', sa.String(20), nullable=True),
        sa.Column('cuenta_puc', sa.String(10), nullable=False),
        sa.Column('cuenta_nombre', sa.String(255), nullable=True),
        sa.Column('tercero_nit', sa.String(20), nullable=True),
        sa.Column('descripcion', sa.Text(), nullable=True),
        sa.Column('debito', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('credito', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['transaction_posted_id'], ['transactions_posted.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_journal_entry_lines_posted_id', 'journal_entry_lines', ['transaction_posted_id'])
    op.create_index('ix_journal_entry_lines_cuenta_puc', 'journal_entry_lines', ['cuenta_puc'])
    op.create_index('ix_journal_entry_lines_tercero_nit', 'journal_entry_lines', ['tercero_nit'])

    # ── process_jobs ──
    op.create_table(
        'process_jobs',
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('ingest_id', sa.String(50), nullable=False),
        sa.Column('status', process_status, nullable=False, server_default='QUEUED'),
        sa.Column('current_stage', sa.String(50), nullable=True),
        sa.Column('current_agent', sa.String(50), nullable=True),
        sa.Column('progress', sa.Integer(), server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('agent_log', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['ingest_id'], ['ingest_jobs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_process_jobs_id', 'process_jobs', ['id'])
    op.create_index('ix_process_jobs_ingest_id', 'process_jobs', ['ingest_id'])

    # ── audit_logs ──
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('entity_id', sa.String(50), nullable=True),
        sa.Column('entity_type', sa.String(50), nullable=True),
        sa.Column('details', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_entity_id', 'audit_logs', ['entity_id'])

    # ── company_settings ──
    op.create_table(
        'company_settings',
        sa.Column('nit', sa.String(20), nullable=False, comment='Empresa NIT (tenant identifier)'),
        sa.Column('nombre', sa.String(255), nullable=True),
        sa.Column('ciudad', sa.String(100), nullable=True),
        sa.Column('codigo_ciiu', sa.String(10), nullable=True, comment='CIIU economic activity code'),
        sa.Column('iva_responsable', sa.Boolean(), nullable=False, server_default=sa.text('true'),
                  comment='True=régimen común (IVA applies), False=régimen simplificado'),
        sa.Column('tasa_retefuente_servicios', sa.Numeric(8, 6), nullable=False, server_default='0.110000'),
        sa.Column('tasa_retefuente_bienes', sa.Numeric(8, 6), nullable=False, server_default='0.030000'),
        sa.Column('tasa_retefuente_arrendamiento', sa.Numeric(8, 6), nullable=False, server_default='0.100000'),
        sa.Column('tasa_reteica', sa.Numeric(8, 6), nullable=False, server_default='0.006900',
                  comment='Municipal ICA retention rate'),
        sa.Column('tasa_iva_general', sa.Numeric(8, 6), nullable=False, server_default='0.190000'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('nit'),
    )

    # ── reteica_tarifas ──
    op.create_table(
        'reteica_tarifas',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('municipio', sa.String(100), nullable=False, comment='Lowercase normalized city name'),
        sa.Column('ciiu_seccion', sa.String(10), nullable=False,
                  comment="CIIU section letter (A-U) or 'general'"),
        sa.Column('tasa', sa.Numeric(10, 8), nullable=False,
                  comment='Rate as decimal fraction, e.g. 0.00966 for 0.966%'),
        sa.Column('fuente', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_reteica_tarifas_municipio', 'reteica_tarifas', ['municipio'])

    # Seed initial ReteICA rates for major Colombian cities.
    # Rates are expressed as decimal fractions (0.00966 = 0.966%).
    # Sources: Acuerdos municipales vigentes.
    # 'general' ciiu_seccion = city-wide default; use section letter for specific sectors.
    reteica_data = op.get_bind()
    reteica_data.execute(sa.text("""
        INSERT INTO reteica_tarifas (municipio, ciiu_seccion, tasa, fuente) VALUES
        -- Nacional (fallback for any unlisted city)
        ('general',        'general', 0.00690000, 'Tarifa nacional de referencia'),
        -- Bogotá (Acuerdo 065 de 2002 y actualizaciones)
        ('bogota',         'general', 0.00966000, 'Acuerdo 065 Bogotá 2016'),
        ('bogota',         'G',       0.00690000, 'Acuerdo 065 Bogotá 2016 - Comercio'),
        ('bogota',         'C',       0.00414000, 'Acuerdo 065 Bogotá 2016 - Industria'),
        ('bogota',         'F',       0.00690000, 'Acuerdo 065 Bogotá 2016 - Construcción'),
        ('bogota',         'J',       0.00966000, 'Acuerdo 065 Bogotá 2016 - Tecnología'),
        ('bogota',         'K',       0.00966000, 'Acuerdo 065 Bogotá 2016 - Financiero'),
        -- Medellín (Acuerdo 67 de 2012)
        ('medellin',       'general', 0.00690000, 'Acuerdo 67 Medellín 2012'),
        ('medellin',       'G',       0.00500000, 'Acuerdo 67 Medellín 2012 - Comercio'),
        ('medellin',       'C',       0.00350000, 'Acuerdo 67 Medellín 2012 - Industria'),
        ('medellin',       'J',       0.00690000, 'Acuerdo 67 Medellín 2012 - Tecnología'),
        -- Cali (Acuerdo 0294 de 2014)
        ('cali',           'general', 0.00690000, 'Acuerdo 0294 Cali 2014'),
        ('cali',           'G',       0.00414000, 'Acuerdo 0294 Cali 2014 - Comercio'),
        ('cali',           'C',       0.00276000, 'Acuerdo 0294 Cali 2014 - Industria'),
        ('cali',           'J',       0.00690000, 'Acuerdo 0294 Cali 2014 - Tecnología'),
        -- Barranquilla (Decreto 1122 de 2016)
        ('barranquilla',   'general', 0.00828000, 'Decreto 1122 Barranquilla 2016'),
        ('barranquilla',   'G',       0.00552000, 'Decreto 1122 Barranquilla 2016 - Comercio'),
        ('barranquilla',   'C',       0.00414000, 'Decreto 1122 Barranquilla 2016 - Industria'),
        -- Bucaramanga (Acuerdo 010 de 2015)
        ('bucaramanga',    'general', 0.00966000, 'Acuerdo 010 Bucaramanga 2015'),
        ('bucaramanga',    'G',       0.00690000, 'Acuerdo 010 Bucaramanga 2015 - Comercio'),
        -- Cartagena (Decreto 1272 de 2016)
        ('cartagena',      'general', 0.00828000, 'Decreto 1272 Cartagena 2016'),
        ('cartagena',      'G',       0.00552000, 'Decreto 1272 Cartagena 2016 - Comercio'),
        -- Manizales
        ('manizales',      'general', 0.00690000, 'Estatuto Tributario Manizales'),
        -- Pereira
        ('pereira',        'general', 0.00690000, 'Estatuto Tributario Pereira'),
        -- Cúcuta
        ('cucuta',         'general', 0.00828000, 'Estatuto Tributario Cúcuta'),
        -- Ibagué
        ('ibague',         'general', 0.00690000, 'Estatuto Tributario Ibagué')
    """))


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('reteica_tarifas')
    op.drop_table('company_settings')
    op.drop_table('audit_logs')
    op.drop_table('process_jobs')
    op.drop_table('journal_entry_lines')
    op.drop_table('transactions_posted')
    op.drop_table('transactions_pending')
    op.drop_table('ingest_jobs')
    op.drop_table('cuentas_puc')
    op.drop_table('terceros')

    # Drop enum types via raw SQL (most reliable)
    for enum_name in ['processstatus', 'transactionstatus', 'ingeststatus', 'naturalezacuenta', 'tercerotipo']:
        op.execute(sa.text(f'DROP TYPE IF EXISTS {enum_name}'))
