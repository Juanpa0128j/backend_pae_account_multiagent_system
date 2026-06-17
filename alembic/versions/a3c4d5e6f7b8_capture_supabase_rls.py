"""capture Supabase RLS (enable + tenant policies) in code

Until now RLS was set up out-of-band (Supabase dashboard) and lived ONLY in prod:
not reproducible from code, absent on fresh DBs. This migration captures it so a
rebuilt database (or a new environment) reproduces the same posture.

GOTCHA — portability: the policies reference ``auth.uid()`` (Supabase ``auth``
schema), which does NOT exist on plain Postgres (local docker / CI pgvector).
Creating these policies there would fail. So the whole migration is GUARDED to
run only where ``auth.uid()`` exists (Supabase); on plain Postgres it is a no-op
(RLS stays off, exactly as it is today on dev/CI — tests connect without an auth
context and the backend bypasses RLS via the service role anyway).

The policies are reproduced VERBATIM from prod (same predicate, same unwrapped
``auth.uid()``) → zero behavioural change when applied. Idempotent: each policy
is dropped-if-exists before creation; ENABLE RLS is itself idempotent.

Revision ID: a3c4d5e6f7b8
Revises: f2b3c4d5e6a7
Create Date: 2026-06-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a3c4d5e6f7b8"
down_revision: Union[str, Sequence[str], None] = "f2b3c4d5e6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All public tables that have RLS enabled in prod (29).
_RLS_TABLES = [
    "ajustes_fiscales",
    "alembic_version",
    "audit_logs",
    "chat_messages",
    "chat_sessions",
    "company_puc_config",
    "company_rate_overrides",
    "company_settings",
    "cuentas_puc",
    "financial_statement_lineage",
    "financial_statements",
    "ingest_jobs",
    "journal_entry_lines",
    "national_rates",
    "perdidas_fiscales_acumuladas",
    "process_jobs",
    "reteica_tarifas",
    "special_tax_accumulators",
    "special_taxes",
    "tarifas_renta",
    "tax_base_minima",
    "tax_concepts",
    "tax_declaration_drafts",
    "terceros",
    "transactions_pending",
    "transactions_posted",
    "user_company",
    "uvt_values",
    "vector_documents",
]


# Tenant predicate: the row's <col> must belong to a company the current auth user
# is linked to (verbatim from prod — auth.uid() kept unwrapped).
def _tenant(col: str) -> str:
    return (
        f"(({col})::text IN ( SELECT user_company.company_nit\n"
        "   FROM user_company\n"
        "  WHERE (((user_company.user_id)::text = (auth.uid())::text) "
        "AND (user_company.deleted_at IS NULL))))"
    )


# Nested predicate: child row belongs via <parent> (which is itself tenant-scoped).
def _nested(child_col: str, parent_table: str, parent_pk: str, parent_col: str) -> str:
    return (
        f"(({child_col})::text IN ( SELECT {parent_table}.{parent_pk}\n"
        f"   FROM {parent_table}\n"
        f"  WHERE ({_tenant_inner(parent_table, parent_col)})))"
    )


def _tenant_inner(tbl: str, col: str) -> str:
    return (
        f"({tbl}.{col})::text IN ( SELECT user_company.company_nit\n"
        "           FROM user_company\n"
        "          WHERE (((user_company.user_id)::text = (auth.uid())::text) "
        "AND (user_company.deleted_at IS NULL)))"
    )


# Direct tenant-scoped tables: (table, tenant_column, commands).
_DIRECT = [
    ("ajustes_fiscales", "company_nit", ["INSERT", "SELECT"]),
    ("audit_logs", "company_nit", ["SELECT"]),
    ("chat_sessions", "company_nit", ["INSERT", "SELECT"]),
    ("company_puc_config", "company_nit", ["INSERT", "SELECT", "UPDATE"]),
    ("company_rate_overrides", "company_nit", ["INSERT", "SELECT", "UPDATE"]),
    ("company_settings", "nit", ["INSERT", "SELECT", "UPDATE"]),
    ("financial_statements", "entity_nit", ["INSERT", "SELECT"]),
    ("ingest_jobs", "company_nit", ["INSERT", "SELECT"]),
    ("journal_entry_lines", "company_nit", ["INSERT", "SELECT"]),
    ("perdidas_fiscales_acumuladas", "company_nit", ["INSERT", "SELECT"]),
    ("special_tax_accumulators", "company_nit", ["INSERT", "SELECT"]),
    ("special_taxes", "company_nit", ["INSERT", "SELECT", "UPDATE"]),
    ("tax_declaration_drafts", "company_nit", ["INSERT", "SELECT", "UPDATE"]),
    ("transactions_pending", "company_nit", ["INSERT", "SELECT", "UPDATE", "DELETE"]),
    ("transactions_posted", "company_nit", ["INSERT", "SELECT", "UPDATE", "DELETE"]),
]

# (table, predicate, commands) for the non-direct cases.
_SPECIAL = [
    (
        "chat_messages",
        _nested("session_id", "chat_sessions", "id", "company_nit"),
        ["INSERT", "SELECT"],
    ),
    (
        "process_jobs",
        _nested("ingest_id", "ingest_jobs", "id", "company_nit"),
        ["INSERT", "SELECT"],
    ),
    ("user_company", "((user_id)::text = (auth.uid())::text)", ["INSERT", "SELECT"]),
]


def _all_policies():
    """Yield (table, policy_name, cmd, predicate) for every policy."""
    for table, col, cmds in _DIRECT:
        for cmd in cmds:
            yield table, f"{table}_{cmd.lower()}", cmd, _tenant(col)
    for table, predicate, cmds in _SPECIAL:
        for cmd in cmds:
            yield table, f"{table}_{cmd.lower()}", cmd, predicate


def _supabase(bind) -> bool:
    """True only on Supabase (auth.uid() exists). False on plain Postgres."""
    return bool(
        bind.exec_driver_sql(
            "SELECT to_regprocedure('auth.uid()') IS NOT NULL"
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _supabase(bind):
        # Plain Postgres (CI/dev): no auth.uid(), no Supabase auth model → skip.
        return
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    for table, name, cmd, predicate in _all_policies():
        clause = "WITH CHECK" if cmd == "INSERT" else "USING"
        op.execute(f"DROP POLICY IF EXISTS {name} ON public.{table}")
        op.execute(
            f"CREATE POLICY {name} ON public.{table} AS PERMISSIVE "
            f"FOR {cmd} TO public {clause} ({predicate})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _supabase(bind):
        return
    for table, name, _cmd, _pred in _all_policies():
        op.execute(f"DROP POLICY IF EXISTS {name} ON public.{table}")
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
