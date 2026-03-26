"""initial_schema

Consolidated migration: all tables, indexes, and seed data for the PAE
accounting system. Absorbs c3f8a2d91b5e, d4e5f6a7b8c9, and b232aff042b8.
Also adds ICA / Renta columns and full Santa Marta ReteICA tariffs (Plan step 1).

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
    """Create all tables for the PAE accounting system (consolidated)."""

    # Enable pgvector extension (no-op if already enabled — safe on Supabase)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── terceros ──
    # Final state: unique index ix_terceros_nit (no separate UniqueConstraint)
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
    )
    op.create_index('ix_terceros_nit', 'terceros', ['nit'], unique=True)

    # ── cuentas_puc ──
    # Final state: unique index ix_cuentas_puc_codigo (no separate UniqueConstraint)
    op.create_table(
        'cuentas_puc',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('codigo', sa.String(10), nullable=False),
        sa.Column('nombre', sa.String(255), nullable=False),
        sa.Column('clase', sa.Integer(), nullable=False,
                  comment='1=Activo,2=Pasivo,3=Patrimonio,4=Ingreso,5=Gasto,6=Costo'),
        sa.Column('grupo', sa.String(4), nullable=True),
        sa.Column('cuenta', sa.String(6), nullable=True),
        sa.Column('subcuenta', sa.String(8), nullable=True),
        sa.Column('naturaleza', naturaleza_cuenta, nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=True),
        sa.Column('activa', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cuentas_puc_codigo', 'cuentas_puc', ['codigo'], unique=True)

    # ── ingest_jobs ──
    # Includes document_type and pathway columns (absorbed from b232aff042b8)
    op.create_table(
        'ingest_jobs',
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_path', sa.String(500), nullable=True),
        sa.Column('status', ingest_status, nullable=False, server_default='PENDING_PROCESSING'),
        sa.Column('document_type', sa.String(50), nullable=True,
                  comment='DocumentType enum value'),
        sa.Column('pathway', sa.String(30), nullable=True,
                  comment='build_from_scratch | work_with_existing'),
        sa.Column('raw_preview', postgresql.JSONB(), nullable=True,
                  comment='Quick preview of extracted data'),
        sa.Column('extraction_errors', postgresql.JSONB(), nullable=True,
                  comment='List of error messages'),
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
        sa.Column('items', postgresql.JSONB(), nullable=True,
                  comment='Line items from document'),
        sa.Column('raw_data', postgresql.JSONB(), nullable=True,
                  comment='Full Gemini extraction result'),
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
    # Final index name: ix_transactions_posted_transaction_pending_id (absorbed from b232aff042b8)
    # Includes ica and provision_renta columns (Plan step 1b)
    op.create_table(
        'transactions_posted',
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('transaction_pending_id', sa.String(50), nullable=False),
        sa.Column('cuenta_puc', sa.String(10), nullable=False),
        sa.Column('puc_descripcion', sa.String(255), nullable=True),
        sa.Column('retefuente', sa.Numeric(15, 2), server_default='0'),
        sa.Column('reteica', sa.Numeric(15, 2), server_default='0'),
        sa.Column('iva', sa.Numeric(15, 2), server_default='0'),
        sa.Column('ica', sa.Numeric(15, 2), server_default='0'),
        sa.Column('provision_renta', sa.Numeric(15, 2), server_default='0'),
        sa.Column('neto_a_pagar', sa.Numeric(15, 2), server_default='0'),
        sa.Column('journal_entries_json', postgresql.JSONB(), nullable=True),
        sa.Column('tax_references', postgresql.JSONB(), nullable=True,
                  comment='Legal references: Art. 383 ET, etc.'),
        sa.Column('agent_reasoning', postgresql.JSONB(), nullable=True,
                  comment='Agent decision log per step'),
        sa.Column('status', transaction_status, nullable=False, server_default='POSTED'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['transaction_pending_id'], ['transactions_pending.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_transactions_posted_id', 'transactions_posted', ['id'])
    op.create_index('ix_transactions_posted_transaction_pending_id', 'transactions_posted',
                    ['transaction_pending_id'])
    op.create_index('ix_transactions_posted_cuenta_puc', 'transactions_posted', ['cuenta_puc'])

    # ── journal_entry_lines ──
    # Final index name: ix_journal_entry_lines_transaction_posted_id (absorbed from b232aff042b8)
    op.create_table(
        'journal_entry_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('transaction_posted_id', sa.String(50), nullable=False),
        sa.Column('fecha', sa.DateTime(timezone=True), nullable=False),
        sa.Column('comprobante', sa.String(20), nullable=True,
                  comment='Voucher/receipt number'),
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
    op.create_index('ix_journal_entry_lines_transaction_posted_id', 'journal_entry_lines',
                    ['transaction_posted_id'])
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
        sa.Column('progress', sa.Integer(), server_default='0',
                  comment='0-100 percent'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('agent_log', postgresql.JSONB(), nullable=True,
                  comment='Timeline of agent steps'),
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
        sa.Column('action', sa.String(100), nullable=False,
                  comment='e.g. transaction_created, agent_ran'),
        sa.Column('entity_id', sa.String(50), nullable=True),
        sa.Column('entity_type', sa.String(50), nullable=True,
                  comment='e.g. transaction, job, ingest'),
        sa.Column('details', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_entity_id', 'audit_logs', ['entity_id'])

    # ── company_settings ──
    # Includes tasa_ica and tasa_renta columns (Plan step 1a)
    op.create_table(
        'company_settings',
        sa.Column('nit', sa.String(20), nullable=False,
                  comment='Empresa NIT (tenant identifier)'),
        sa.Column('nombre', sa.String(255), nullable=True),
        sa.Column('ciudad', sa.String(100), nullable=True),
        sa.Column('codigo_ciiu', sa.String(10), nullable=True,
                  comment='CIIU economic activity code'),
        sa.Column('iva_responsable', sa.Boolean(), nullable=False, server_default=sa.text('true'),
                  comment='True=régimen común (IVA applies), False=régimen simplificado'),
        sa.Column('tasa_retefuente_servicios', sa.Numeric(8, 6), nullable=False,
                  server_default='0.110000'),
        sa.Column('tasa_retefuente_bienes', sa.Numeric(8, 6), nullable=False,
                  server_default='0.030000'),
        sa.Column('tasa_retefuente_arrendamiento', sa.Numeric(8, 6), nullable=False,
                  server_default='0.100000'),
        sa.Column('tasa_reteica', sa.Numeric(8, 6), nullable=False, server_default='0.006900',
                  comment='Municipal ICA retention rate'),
        sa.Column('tasa_iva_general', sa.Numeric(8, 6), nullable=False,
                  server_default='0.190000'),
        sa.Column('tasa_ica', sa.Numeric(10, 8), nullable=False, server_default='0.00690000',
                  comment='Tarifa ICA sobre ingresos brutos (Ley 14/1983). Varía por municipio/CIIU.'),
        sa.Column('tasa_renta', sa.Numeric(8, 6), nullable=False, server_default='0.350000',
                  comment='Tarifa impuesto de renta societario — Art. 240 ET, 35% (Ley 2277/2022).'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=True),
        sa.PrimaryKeyConstraint('nit'),
    )

    # ── reteica_tarifas ──
    op.create_table(
        'reteica_tarifas',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('municipio', sa.String(100), nullable=False,
                  comment='Lowercase normalized city name'),
        sa.Column('ciiu_seccion', sa.String(10), nullable=False,
                  comment="CIIU section letter (A-U) or 'general'"),
        sa.Column('tasa', sa.Numeric(10, 8), nullable=False,
                  comment='Rate as decimal fraction, e.g. 0.00966 for 0.966%'),
        sa.Column('fuente', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_reteica_tarifas_municipio', 'reteica_tarifas', ['municipio'])

    # ── vector_documents ──
    # Final state (absorbing c3f8a2d91b5e + d4e5f6a7b8c9 + b232aff042b8):
    # embedding and content_tsv were added then dropped — created here in final form.
    op.execute("""
        CREATE TABLE IF NOT EXISTS vector_documents (
            id              VARCHAR NOT NULL,
            collection_name VARCHAR(255) NOT NULL,
            content         TEXT NOT NULL,
            metadata        JSONB DEFAULT '{}',
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (collection_name, id)
        )
    """)

    # ── financial_statements ── (absorbed from b232aff042b8)
    op.create_table(
        'financial_statements',
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('ingest_id', sa.String(50), nullable=False),
        sa.Column('statement_type', sa.String(50), nullable=False,
                  comment='balance_general | estado_resultados | libro_auxiliar'),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('entity_nit', sa.String(20), nullable=True),
        sa.Column('data', sa.JSON().with_variant(
            postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
            nullable=False,
            comment='Full parsed financial statement data'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['ingest_id'], ['ingest_jobs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_financial_statements_id'), 'financial_statements', ['id'])
    op.create_index(op.f('ix_financial_statements_ingest_id'), 'financial_statements',
                    ['ingest_id'])

    # ── Seed: ReteICA rates ──────────────────────────────────────────────────
    # Rates are expressed as decimal fractions (e.g. 0.00966 = 9.66‰ = 0.966%).
    #
    # IMPORTANT: ReteICA is a 100% territorial tax — each of Colombia's ~1,100
    # municipalities sets its own rates via acuerdos municipales. There is no
    # single national table. Entries marked [REFERENCIA] are estimated from
    # national ranges (4.14‰–13.8‰) and must be verified against each city's
    # current estatuto tributario before use in production.
    #
    # CIIU sections (ISO 3166): A=Agriculture, C=Manufacturing, F=Construction,
    #   G=Commerce, H=Transport, I=Hotels/Restaurants, J=Tech/Info, K=Finance,
    #   L=Real estate, M=Professional services, N=Admin services, P=Education,
    #   Q=Health, R=Entertainment, S=Other services
    op.get_bind().execute(sa.text("""
        INSERT INTO reteica_tarifas (municipio, ciiu_seccion, tasa, fuente) VALUES

        -- ─── Nacional fallback (used when city not in table) ────────────────
        ('general', 'general', 0.00690000, 'Tarifa de referencia nacional - verificar estatuto municipal'),

        -- ─── Bogotá (Acuerdo 050 de 2024, fuente: haciendabogota.gov.co) ───
        ('bogota', 'C',       0.00414000, 'Acuerdo 050 Bogotá 2024 - Industria alimentos/fármacos 4.14‰'),
        ('bogota', 'general', 0.00966000, 'Acuerdo 050 Bogotá 2024 - Servicios generales 9.66‰'),
        ('bogota', 'G',       0.01104000, 'Acuerdo 050 Bogotá 2024 - Comercio general 11.04‰'),
        ('bogota', 'F',       0.00690000, 'Acuerdo 050 Bogotá 2024 - Construcción 6.9‰'),
        ('bogota', 'H',       0.00414000, 'Acuerdo 050 Bogotá 2024 - Transporte 4.14‰'),
        ('bogota', 'I',       0.01380000, 'Acuerdo 050 Bogotá 2024 - Hoteles/Restaurantes 13.8‰'),
        ('bogota', 'J',       0.00966000, 'Acuerdo 050 Bogotá 2024 - Tecnología/Info 9.66‰'),
        ('bogota', 'K',       0.01104000, 'Acuerdo 050 Bogotá 2024 - Financiero 11.04‰'),
        ('bogota', 'L',       0.00966000, 'Acuerdo 050 Bogotá 2024 - Inmobiliario 9.66‰'),
        ('bogota', 'M',       0.00690000, 'Acuerdo 050 Bogotá 2024 - Profesional/Consultoría 6.9‰'),
        ('bogota', 'N',       0.00966000, 'Acuerdo 050 Bogotá 2024 - Servicios administrativos 9.66‰'),
        ('bogota', 'P',       0.00700000, 'Acuerdo 050 Bogotá 2024 - Educación privada 7.0‰'),
        ('bogota', 'Q',       0.00966000, 'Acuerdo 050 Bogotá 2024 - Salud 9.66‰'),

        -- ─── Medellín (Acuerdo 093 de 2023, fuente: medellin.gov.co) ────────
        ('medellin', 'general', 0.00200000, 'Acuerdo 093 Medellín 2023 - Tarifa única 2‰'),

        -- ─── Cali (Acuerdo 0294 de 2014, retención = 100% del ICA) ──────────
        ('cali', 'general', 0.00966000, 'Acuerdo 0294 Cali 2014 - Servicios generales 9.66‰'),
        ('cali', 'C',       0.00414000, 'Acuerdo 0294 Cali 2014 - Industria 4.14‰'),
        ('cali', 'G',       0.00690000, 'Acuerdo 0294 Cali 2014 - Comercio 6.9‰'),
        ('cali', 'F',       0.00690000, 'Acuerdo 0294 Cali 2014 - Construcción 6.9‰'),
        ('cali', 'H',       0.00414000, 'Acuerdo 0294 Cali 2014 - Transporte 4.14‰'),
        ('cali', 'I',       0.00966000, 'Acuerdo 0294 Cali 2014 - Hoteles/Restaurantes 9.66‰'),
        ('cali', 'J',       0.00966000, 'Acuerdo 0294 Cali 2014 - Tecnología 9.66‰'),
        ('cali', 'K',       0.01104000, 'Acuerdo 0294 Cali 2014 - Financiero 11.04‰'),
        ('cali', 'M',       0.00966000, 'Acuerdo 0294 Cali 2014 - Profesional 9.66‰'),
        ('cali', 'P',       0.00414000, 'Acuerdo 0294 Cali 2014 - Educación 4.14‰'),

        -- ─── Barranquilla (Acuerdo 006 de 2023, fuente: barranquilla.gov.co) ─
        ('barranquilla', 'general', 0.00800000, 'Acuerdo 006 Barranquilla 2023 - Servicios generales 8.0‰'),
        ('barranquilla', 'C',       0.00740000, 'Acuerdo 006 Barranquilla 2023 - Industria alimentos/fármacos 7.4‰'),
        ('barranquilla', 'G',       0.00540000, 'Acuerdo 006 Barranquilla 2023 - Comercio alimentos 5.4‰'),
        ('barranquilla', 'F',       0.00800000, 'Acuerdo 006 Barranquilla 2023 - Construcción 8.0‰'),
        ('barranquilla', 'H',       0.00800000, 'Acuerdo 006 Barranquilla 2023 - Transporte/Salud 8.0‰'),
        ('barranquilla', 'I',       0.01000000, 'Acuerdo 006 Barranquilla 2023 - Hoteles/Restaurantes 10.0‰'),
        ('barranquilla', 'J',       0.02000000, 'Acuerdo 006 Barranquilla 2023 - Telecomunicaciones 20.0‰'),
        ('barranquilla', 'K',       0.01160000, 'Acuerdo 006 Barranquilla 2023 - Otros servicios 11.6‰'),
        ('barranquilla', 'P',       0.00450000, 'Acuerdo 006 Barranquilla 2023 - Educación 4.5‰'),

        -- ─── Bucaramanga (tarifa única 5‰ para TODAS las actividades) ────────
        ('bucaramanga', 'general', 0.00500000, 'Estatuto Tributario Bucaramanga - Tarifa única 5‰'),

        -- ─── Cartagena [REFERENCIA - verificar Estatuto Tributario Distrital] ─
        ('cartagena', 'general', 0.00828000, 'Referencia - Cartagena ~8.28‰ servicios - verificar'),
        ('cartagena', 'G',       0.00552000, 'Referencia - Cartagena ~5.52‰ comercio - verificar'),
        ('cartagena', 'C',       0.00414000, 'Referencia - Cartagena ~4.14‰ industria - verificar'),

        -- ─── Pereira (retención = 100% del ICA) [REFERENCIA] ─────────────────
        ('pereira', 'general', 0.00966000, 'Referencia - Pereira ~9.66‰ servicios - verificar'),
        ('pereira', 'G',       0.00690000, 'Referencia - Pereira ~6.9‰ comercio - verificar'),
        ('pereira', 'C',       0.00414000, 'Referencia - Pereira ~4.14‰ industria - verificar'),

        -- ─── Manizales [REFERENCIA] ───────────────────────────────────────────
        ('manizales', 'general', 0.00690000, 'Referencia - Manizales ~6.9‰ - verificar estatuto'),

        -- ─── Cúcuta [REFERENCIA] ─────────────────────────────────────────────
        ('cucuta', 'general', 0.00828000, 'Referencia - Cúcuta ~8.28‰ - verificar estatuto'),

        -- ─── Ibagué [REFERENCIA] ─────────────────────────────────────────────
        ('ibague', 'general', 0.00690000, 'Referencia - Ibagué ~6.9‰ - verificar estatuto'),

        -- ─── Santa Marta — Acuerdo 018 del 30-dic-2025 (vigente año gravable 2026) ──
        -- Source: Acuerdo 018/2025 — Concejo Distrital de Santa Marta.
        -- Modifica parcialmente Acuerdo 004/2016. Art. 88: ReteICA = tarifa ICA
        -- de la actividad; actividad desconocida → tarifa máxima (14‰).
        -- SM uses own activity codes (101-405); CIIU sections are nearest mapping.
        ('santa marta', 'C',       0.00700000, 'Acuerdo 018 del 30-dic-2025, Art. 69 — Industria manufacturera (códigos 105-110) 7‰. Concejo Distrital de Santa Marta. Vigente 2026.'),
        ('santa marta', 'F',       0.00500000, 'Acuerdo 018 del 30-dic-2025, Arts. 69-71 código 301 — Construcción 5‰. Concejo Distrital de Santa Marta. Vigente 2026.'),
        ('santa marta', 'G',       0.00700000, 'Acuerdo 018 del 30-dic-2025, Art. 70 códigos 202-203 — Comercio reparación vehículos/mayorista en comisión 7‰. Vigente 2026.'),
        ('santa marta', 'H',       0.00700000, 'Acuerdo 018 del 30-dic-2025, Art. 71 — Transporte y almacenamiento (301=5‰/307=10‰; 7‰ punto medio). Vigente 2026.'),
        ('santa marta', 'I',       0.00500000, 'Acuerdo 018 del 30-dic-2025, Art. 71 código 301 — Alojamiento y servicios de comida 5‰. Vigente 2026.'),
        ('santa marta', 'J',       0.00700000, 'Acuerdo 018 del 30-dic-2025, Art. 71 códigos 302(6‰)/304(7‰) — Info y comunicaciones 7‰ conservador. Vigente 2026.'),
        ('santa marta', 'K',       0.01400000, 'Acuerdo 018 del 30-dic-2025, Art. 21 (modifica Art. 71) — Servicios financieros, seguros, bancos 14‰. Vigente 2026.'),
        ('santa marta', 'L',       0.01000000, 'Acuerdo 018 del 30-dic-2025, Art. 71 código 307 — Actividades inmobiliarias 10‰. Vigente 2026.'),
        ('santa marta', 'M',       0.00700000, 'Acuerdo 018 del 30-dic-2025, Art. 71 código 304 — Servicios profesionales, jurídicos, consultoría 7‰. Vigente 2026.'),
        ('santa marta', 'N',       0.00800000, 'Acuerdo 018 del 30-dic-2025, Art. 71 códigos 304(7‰)/306(8‰) — Servicios a edificios y paisajismo 8‰. Vigente 2026.'),
        ('santa marta', 'P',       0.00500000, 'Acuerdo 018 del 30-dic-2025, Art. 71 código 301 — Educación 5‰. Vigente 2026.'),
        ('santa marta', 'Q',       0.00500000, 'Acuerdo 018 del 30-dic-2025, Art. 71 códigos 301, 303 — Salud y atención médica 5‰. Vigente 2026.'),
        ('santa marta', 'R',       0.00700000, 'Acuerdo 018 del 30-dic-2025, Art. 71 código 305 — Artes, entretenimiento, producción audiovisual 7‰. Vigente 2026.'),
        ('santa marta', 'general', 0.01400000, 'Acuerdo 018 del 30-dic-2025, Art. 88 — Tarifa máxima vigente (actividad no identificada) 14‰. Vigente 2026.'),

        -- ─── Villavicencio [REFERENCIA] ───────────────────────────────────────
        ('villavicencio', 'general', 0.00690000, 'Referencia - Villavicencio ~6.9‰ - verificar estatuto'),

        -- ─── Pasto [REFERENCIA] ───────────────────────────────────────────────
        ('pasto', 'general', 0.00690000, 'Referencia - Pasto ~6.9‰ - verificar estatuto'),

        -- ─── Montería [REFERENCIA] ────────────────────────────────────────────
        ('monteria', 'general', 0.00690000, 'Referencia - Montería ~6.9‰ - verificar estatuto'),

        -- ─── Armenia [REFERENCIA] ─────────────────────────────────────────────
        ('armenia', 'general', 0.00690000, 'Referencia - Armenia ~6.9‰ - verificar estatuto'),

        -- ─── Neiva [REFERENCIA] ───────────────────────────────────────────────
        ('neiva', 'general', 0.00690000, 'Referencia - Neiva ~6.9‰ - verificar estatuto'),

        -- ─── Valledupar [REFERENCIA] ──────────────────────────────────────────
        ('valledupar', 'general', 0.00828000, 'Referencia - Valledupar ~8.28‰ - verificar estatuto'),

        -- ─── Sincelejo [REFERENCIA] ───────────────────────────────────────────
        ('sincelejo', 'general', 0.00690000, 'Referencia - Sincelejo ~6.9‰ - verificar estatuto'),

        -- ─── Popayán [REFERENCIA] ─────────────────────────────────────────────
        ('popayan', 'general', 0.00690000, 'Referencia - Popayán ~6.9‰ - verificar estatuto'),

        -- ─── Tunja [REFERENCIA] ───────────────────────────────────────────────
        ('tunja', 'general', 0.00690000, 'Referencia - Tunja ~6.9‰ - verificar estatuto'),

        -- ─── Florencia [REFERENCIA] ───────────────────────────────────────────
        ('florencia', 'general', 0.00690000, 'Referencia - Florencia ~6.9‰ - verificar estatuto')
    """))

    # ── Seed: new PUC accounts for ICA and Renta (Plan step 1d) ─────────────
    op.get_bind().execute(sa.text("""
        INSERT INTO cuentas_puc (codigo, nombre, clase, naturaleza, descripcion, activa) VALUES
        ('540101', 'Gasto ICA',
         5, 'DEBITO',
         'Impuesto de Industria y Comercio — Ley 14/1983, Decreto 1333/1986. Grava el ejercicio de actividades industriales, comerciales o de servicios en la jurisdicción municipal.',
         true),
        ('240808', 'ICA por Pagar',
         2, 'CREDITO',
         'Pasivo por ICA causado sobre ingresos brutos del período. Ley 14/1983, Art. 33. Contrapartida del gasto ICA (540101).',
         true),
        ('540502', 'Provisión Impuesto de Renta',
         5, 'DEBITO',
         'Provisión periódica del impuesto de renta societario — Art. 240 ET, tarifa general 35% (Ley 2277/2022, vigente año gravable 2023+).',
         true),
        ('240405', 'Impuesto de Renta por Pagar',
         2, 'CREDITO',
         'Pasivo estimado por impuesto de renta del período. Contrapartida de la provisión (540502). Art. 240 ET.',
         true)
        ON CONFLICT (codigo) DO NOTHING
    """))


def downgrade() -> None:
    """Drop all tables (reverse of upgrade)."""
    op.drop_index(op.f('ix_financial_statements_ingest_id'), table_name='financial_statements')
    op.drop_index(op.f('ix_financial_statements_id'), table_name='financial_statements')
    op.drop_table('financial_statements')
    op.execute("DROP TABLE IF EXISTS vector_documents")
    op.drop_table('reteica_tarifas')
    op.drop_table('company_settings')
    op.drop_index('ix_audit_logs_entity_id', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index('ix_process_jobs_ingest_id', table_name='process_jobs')
    op.drop_index('ix_process_jobs_id', table_name='process_jobs')
    op.drop_table('process_jobs')
    op.drop_index('ix_journal_entry_lines_tercero_nit', table_name='journal_entry_lines')
    op.drop_index('ix_journal_entry_lines_cuenta_puc', table_name='journal_entry_lines')
    op.drop_index('ix_journal_entry_lines_transaction_posted_id', table_name='journal_entry_lines')
    op.drop_table('journal_entry_lines')
    op.drop_index('ix_transactions_posted_cuenta_puc', table_name='transactions_posted')
    op.drop_index('ix_transactions_posted_transaction_pending_id', table_name='transactions_posted')
    op.drop_index('ix_transactions_posted_id', table_name='transactions_posted')
    op.drop_table('transactions_posted')
    op.drop_index('ix_transactions_pending_nit_receptor', table_name='transactions_pending')
    op.drop_index('ix_transactions_pending_nit_emisor', table_name='transactions_pending')
    op.drop_index('ix_transactions_pending_ingest_id', table_name='transactions_pending')
    op.drop_index('ix_transactions_pending_id', table_name='transactions_pending')
    op.drop_table('transactions_pending')
    op.drop_index('ix_ingest_jobs_id', table_name='ingest_jobs')
    op.drop_table('ingest_jobs')
    op.drop_index('ix_cuentas_puc_codigo', table_name='cuentas_puc')
    op.drop_table('cuentas_puc')
    op.drop_index('ix_terceros_nit', table_name='terceros')
    op.drop_table('terceros')

    # Drop enum types via raw SQL (most reliable)
    for enum_name in ['processstatus', 'transactionstatus', 'ingeststatus',
                      'naturalezacuenta', 'tercerotipo']:
        op.execute(sa.text(f'DROP TYPE IF EXISTS {enum_name}'))
