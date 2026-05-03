"""Internal services for financial statements query and derived statement generation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.database import IngestStatus
from app.services import db_service
from app.services.nit_utils import normalize_nit

_log = logging.getLogger(__name__)


_REQUIRED_DERIVATION_INPUTS = ("balance_general", "estado_resultados", "libro_auxiliar")
_DERIVED_TARGETS = ("flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros")
_FIRST_LEVEL_TYPES = (
    "balance_general",
    "estado_resultados",
    "libro_auxiliar",
    "libro_diario",
)


def _first_level_type_exists(
    db,
    *,
    company_nit: str,
    statement_type: str,
    period_start: datetime | None,
    period_end: datetime | None,
) -> bool:
    rows = db_service.get_financial_statements(
        db,
        company_nit=company_nit,
        statement_type=statement_type,
        period_start=period_start,
        period_end=period_end,
    )
    return len(rows) > 0


# Sentinel file_path used to mark synthetic IngestJobs created only to satisfy
# the FK constraint on FinancialStatement. Listing endpoints must filter these
# out so the user does not see phantom "uploads" they never made.
SYNTHETIC_INGEST_FILE_PATH = "internal://journal_derived"


def is_synthetic_ingest_job(job) -> bool:
    """True if the IngestJob was created only as an FK target for derived statements."""
    file_path = getattr(job, "file_path", None)
    return file_path == SYNTHETIC_INGEST_FILE_PATH


def _create_derivation_ingest_job(
    db, company_nit: str, period_end: datetime, doc_type: str
):
    """Create a synthetic IngestJob to satisfy the FK constraint on FinancialStatement.

    Tagged with SYNTHETIC_INGEST_FILE_PATH so listing endpoints can filter it out.
    """
    ingest_job = db_service.create_ingest_job(
        db,
        file_name=f"journal_derived_{company_nit}_{period_end.date().isoformat()}_{doc_type}",
        file_path=SYNTHETIC_INGEST_FILE_PATH,
        commit=False,
    )
    ingest_job.status = IngestStatus.COMPLETED
    ingest_job.document_type = doc_type
    ingest_job.pathway = "build_from_scratch"
    return ingest_job


def build_first_level_from_journal_entries(
    db,
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Build and persist first-level FinancialStatement records from JournalEntryLines.

    Creates balance_general, estado_resultados, libro_auxiliar, libro_diario records
    with source_mode='derived_from_journal'. Idempotent: skips any type already present.
    """
    normalized_nit = normalize_nit(company_nit)
    created: dict[str, str] = {}
    skipped: list[str] = []

    # --- Balance General ---
    if not _first_level_type_exists(
        db,
        company_nit=normalized_nit,
        statement_type="balance_general",
        period_start=period_start,
        period_end=period_end,
    ):
        try:
            # get_balance_sheet uses cutoff_date (not start_date/end_date); use period_end as cutoff
            bg_raw = db_service.get_balance_sheet(
                db, cutoff_date=period_end, company_nit=normalized_nit
            )
            bg_data = {
                "tipo": "balance_general",
                "entidad": {"nit": normalized_nit},
                "periodo_inicio": period_start.date().isoformat(),
                "periodo_fin": period_end.date().isoformat(),
                "total_activos": bg_raw.get("assets", 0),
                "total_pasivos": bg_raw.get("liabilities", 0),
                "total_patrimonio": bg_raw.get("total_equity", 0),
                "utilidad_neta": bg_raw.get("net_profit", 0),
                "patrimonio_sin_utilidad": bg_raw.get("equity", 0),
                "cuadre": bg_raw.get("is_balanced", False),
                "moneda": "COP",
                "source": "derived_from_journal",
            }
            ingest_job = _create_derivation_ingest_job(
                db, normalized_nit, period_end, "balance_general"
            )
            stmt = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type="balance_general",
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived_from_journal",
                data=bg_data,
                commit=True,
            )
            created["balance_general"] = stmt.id
        except Exception as exc:
            _log.warning("build_first_level: failed to create balance_general: %s", exc)
            skipped.append("balance_general")
    else:
        skipped.append("balance_general")

    # --- Estado de Resultados ---
    if not _first_level_type_exists(
        db,
        company_nit=normalized_nit,
        statement_type="estado_resultados",
        period_start=period_start,
        period_end=period_end,
    ):
        try:
            er_raw = db_service.get_pnl(
                db,
                company_nit=normalized_nit,
                start_date=period_start,
                end_date=period_end,
            )
            er_data = {
                "tipo": "estado_resultados",
                "entidad": {"nit": normalized_nit},
                "periodo_inicio": period_start.date().isoformat(),
                "periodo_fin": period_end.date().isoformat(),
                "ingresos": er_raw.get("ingresos", []),
                "gastos": er_raw.get("gastos", []),
                "costo_ventas": er_raw.get("costo_ventas", []),
                "utilidad_bruta": er_raw.get("utilidad_bruta", 0),
                "utilidad_neta": er_raw.get("utilidad_neta", 0),
                "moneda": "COP",
                "source": "derived_from_journal",
            }
            ingest_job = _create_derivation_ingest_job(
                db, normalized_nit, period_end, "estado_resultados"
            )
            stmt = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type="estado_resultados",
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived_from_journal",
                data=er_data,
                commit=True,
            )
            created["estado_resultados"] = stmt.id
        except Exception as exc:
            _log.warning(
                "build_first_level: failed to create estado_resultados: %s", exc
            )
            skipped.append("estado_resultados")
    else:
        skipped.append("estado_resultados")

    # --- Libro Auxiliar ---
    if not _first_level_type_exists(
        db,
        company_nit=normalized_nit,
        statement_type="libro_auxiliar",
        period_start=period_start,
        period_end=period_end,
    ):
        try:
            # get_general_ledger returns a List[Dict] directly
            ledger = db_service.get_general_ledger(
                db, period_start, period_end, normalized_nit
            )
            la_data = {
                "tipo": "libro_auxiliar",
                "entidad": {"nit": normalized_nit},
                "periodo_inicio": period_start.date().isoformat(),
                "periodo_fin": period_end.date().isoformat(),
                "accounts": ledger if isinstance(ledger, list) else [],
                "moneda": "COP",
                "source": "derived_from_journal",
            }
            ingest_job = _create_derivation_ingest_job(
                db, normalized_nit, period_end, "libro_auxiliar"
            )
            stmt = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type="libro_auxiliar",
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived_from_journal",
                data=la_data,
                commit=True,
            )
            created["libro_auxiliar"] = stmt.id
        except Exception as exc:
            _log.warning("build_first_level: failed to create libro_auxiliar: %s", exc)
            skipped.append("libro_auxiliar")
    else:
        skipped.append("libro_auxiliar")

    # --- Libro Diario ---
    if not _first_level_type_exists(
        db,
        company_nit=normalized_nit,
        statement_type="libro_diario",
        period_start=period_start,
        period_end=period_end,
    ):
        try:
            journal_lines = db_service.get_journal_entry_lines(
                db,
                company_nit=normalized_nit,
                start_date=period_start,
                end_date=period_end,
            )
            ld_data = {
                "tipo": "libro_diario",
                "entidad": {"nit": normalized_nit},
                "periodo_inicio": period_start.date().isoformat(),
                "periodo_fin": period_end.date().isoformat(),
                "asientos": journal_lines if isinstance(journal_lines, list) else [],
                "moneda": "COP",
                "source": "derived_from_journal",
            }
            ingest_job = _create_derivation_ingest_job(
                db, normalized_nit, period_end, "libro_diario"
            )
            stmt = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type="libro_diario",
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived_from_journal",
                data=ld_data,
                commit=True,
            )
            created["libro_diario"] = stmt.id
        except Exception as exc:
            _log.warning("build_first_level: failed to create libro_diario: %s", exc)
            skipped.append("libro_diario")
    else:
        skipped.append("libro_diario")

    return {
        "status": "built",
        "company_nit": normalized_nit,
        "created": created,
        "skipped": len(skipped),
        "skipped_types": skipped,
    }


class BusinessRuleError(RuntimeError):
    """Raised when business preconditions for derived statements are not met."""


def list_financial_statements(
    *,
    company_nit: str,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    statement_type: str | None = None,
    source_mode: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    """Internal read API for financial statements filtered by company and period.

    If ``db`` is provided (e.g. FastAPI request-scoped session), reuse it.
    Otherwise open a short-lived session.
    """
    normalized_nit = normalize_nit(company_nit)

    owned_session = db is None
    if owned_session:
        db = SessionLocal()
    try:
        rows = db_service.get_financial_statements(
            db,
            company_nit=normalized_nit,
            statement_type=statement_type,
            period_start=period_start,
            period_end=period_end,
            source_mode=source_mode,
        )
        return [
            {
                "id": row.id,
                "ingest_id": row.ingest_id,
                "statement_type": row.statement_type,
                "period_start": (
                    row.period_start.isoformat() if row.period_start else None
                ),
                "period_end": row.period_end.isoformat() if row.period_end else None,
                "entity_nit": row.entity_nit,
                "source_mode": row.source_mode,
                "data": row.data,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    finally:
        if owned_session and db is not None:
            db.close()


def derive_financial_statements(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Derive cashflow/equity changes/notes from BG+ER+LA for one company and period.

    Raises:
        BusinessRuleError: when required source statements are not available.
    """
    normalized_nit = normalize_nit(company_nit)

    db = SessionLocal()
    try:
        source_rows = {}
        for stmt_type in _REQUIRED_DERIVATION_INPUTS:
            rows = db_service.get_financial_statements(
                db,
                company_nit=normalized_nit,
                statement_type=stmt_type,
                period_start=period_start,
                period_end=period_end,
            )
            if not rows:
                raise BusinessRuleError(
                    "Missing required inputs for derivation: "
                    f"{stmt_type}. Required set: {', '.join(_REQUIRED_DERIVATION_INPUTS)}"
                )
            source_rows[stmt_type] = rows[0]

        # Dedup guard: skip derived types that already exist
        existing_derived = {
            stmt_type
            for stmt_type in _DERIVED_TARGETS
            if _first_level_type_exists(
                db,
                company_nit=normalized_nit,
                statement_type=stmt_type,
                period_start=period_start,
                period_end=period_end,
            )
        }
        targets_to_create = [t for t in _DERIVED_TARGETS if t not in existing_derived]
        if not targets_to_create:
            return {"status": "already_derived", "skipped": list(existing_derived)}

        bg = source_rows["balance_general"].data or {}
        er = source_rows["estado_resultados"].data or {}
        la = source_rows["libro_auxiliar"].data or {}

        total_activos = Decimal(str(bg.get("total_activos") or 0))
        total_pasivos = Decimal(str(bg.get("total_pasivos") or 0))
        total_patrimonio = Decimal(str(bg.get("total_patrimonio") or 0))
        utilidad_neta = Decimal(str(er.get("utilidad_neta") or 0))

        # --- Cash Flow Derivation (NIC 7 Indirect Method) ---
        accounts = la.get("lines") or la.get("accounts") or []

        # Calculate ending cash (class 11 = Efectivo y equivalentes)
        efectivo_fin = Decimal("0")
        flujo_inversion = Decimal(
            "0"
        )  # Classes 15-17: Property, Plant & Equipment + Long-term Investments
        flujo_financiacion = Decimal("0")  # Classes 25-27: Long-term Debt + Equity

        if isinstance(accounts, list):
            for acc in accounts:
                codigo = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
                saldo = Decimal(
                    str(
                        acc.get("saldo")
                        or acc.get("saldo_neto")
                        or acc.get("total")
                        or 0
                    )
                )

                # Ending cash position (class 11)
                if codigo.startswith("11"):
                    efectivo_fin += saldo
                # Investment account changes (classes 15-17)
                elif codigo.startswith(("15", "16", "17")):
                    flujo_inversion += saldo
                # Financing account changes (classes 25-27)
                elif codigo.startswith(("25", "26", "27")):
                    flujo_financiacion += saldo

        # Opening cash balance = sum of class-11 journal entry net positions up to
        # the day before period_start. get_balance_sheet does not expose a "cash"
        # key (only class-1 totals), so compute directly from journal lines for
        # accurate first-period derivation.
        prior_efectivo = Decimal("0")
        try:
            prior_lines = (
                db.query(db_service.JournalEntryLine)
                .join(
                    db_service.TransactionPosted,
                    db_service.JournalEntryLine.transaction_posted_id
                    == db_service.TransactionPosted.id,
                )
                .filter(
                    db_service.TransactionPosted.status
                    == db_service.TransactionStatus.POSTED,
                    db_service.JournalEntryLine.fecha
                    <= (period_start - timedelta(days=1)),
                    db_service.JournalEntryLine.company_nit == normalized_nit,
                )
                .all()
            )
            for line in prior_lines:
                code = str(line.cuenta_puc or "")
                if code.startswith("11"):
                    prior_efectivo += (line.debito or Decimal("0")) - (
                        line.credito or Decimal("0")
                    )
        except Exception as exc:
            _log.warning(
                "Cash flow derivation: failed to compute prior cash for %s: %s",
                normalized_nit,
                exc,
            )

        efectivo_inicio = prior_efectivo

        # NIC 7 Cash Flow Identity: Ending = Opening + Operating + Investing + Financing
        # For indirect method with simplified derivation:
        flujo_operacion = utilidad_neta  # Base from P&L
        aumento_disminucion = efectivo_fin - efectivo_inicio

        # Verify the identity (may not hold exactly in derived statements due to rounding/schema gaps)
        expected_fin = (
            efectivo_inicio + flujo_operacion + flujo_inversion + flujo_financiacion
        )
        verificacion = abs(efectivo_fin - expected_fin) < Decimal("0.01")

        flujo_data = {
            "tipo": "flujo_de_caja",
            "entidad": {"nit": normalized_nit},
            "periodo_inicio": period_start.date().isoformat(),
            "periodo_fin": period_end.date().isoformat(),
            "metodo": "indirecto",
            "base_presentacion": "NIIF_pymes",
            "flujo_neto_operacion": float(flujo_operacion),
            "flujo_neto_inversion": float(flujo_inversion),
            "flujo_neto_financiacion": float(flujo_financiacion),
            "aumento_disminucion_neto": float(aumento_disminucion),
            "efectivo_inicio_periodo": float(efectivo_inicio),
            "efectivo_fin_periodo": float(efectivo_fin),
            "verificacion": verificacion,
            "moneda": "COP",
            "informacion_adicional": {
                "derivation_basis": [
                    "balance_general",
                    "estado_resultados",
                    "libro_auxiliar",
                ],
                "rule_version": "v2",
                "nic7_identity": {
                    "expected_fin": float(expected_fin),
                    "actual_fin": float(efectivo_fin),
                    "tolerance": 0.01,
                },
                "limitations": (
                    "Derived from GL aggregates; investment/financing flows are simplified "
                    "GL account class sums. Opening balance retrieved from prior period balance sheet; "
                    "may be unavailable for initial company periods. Identity verification is best-effort."
                ),
            },
        }

        cambios_data = {
            "tipo": "cambios_patrimonio",
            "entidad": {"nit": normalized_nit},
            "periodo_inicio": period_start.date().isoformat(),
            "periodo_fin": period_end.date().isoformat(),
            "componentes": [
                {
                    "concepto_patrimonio": "resultado_ejercicio",
                    "saldo_inicial": float(total_patrimonio - utilidad_neta),
                    "movimientos": [
                        {
                            "concepto": "utilidad_neta_del_periodo",
                            "valor": float(utilidad_neta),
                        }
                    ],
                    "saldo_final": float(total_patrimonio),
                }
            ],
            "total_patrimonio_inicio": float(total_patrimonio - utilidad_neta),
            "total_patrimonio_fin": float(total_patrimonio),
            "moneda": "COP",
            "informacion_adicional": {
                "activos": float(total_activos),
                "pasivos": float(total_pasivos),
                "rule_version": "v1",
            },
        }

        notas_data = {
            "tipo": "notas_estados_financieros",
            "entidad": {"nit": normalized_nit},
            "periodo_inicio": period_start.date().isoformat(),
            "periodo_fin": period_end.date().isoformat(),
            "base_presentacion": "NIIF_pymes",
            "moneda_funcional": "COP",
            "hipotesis_negocio_en_marcha": True,
            "notas": [
                {
                    "numero_nota": "1",
                    "titulo": "Base de preparación y trazabilidad",
                    "categoria": "politicas_contables",
                    "contenido_resumido": (
                        "Notas derivadas automáticamente desde balance general, estado de "
                        "resultados y libro auxiliar para el mismo periodo y empresa."
                    ),
                    "cifras_relevantes": [
                        {"concepto": "total_activos", "valor": float(total_activos)},
                        {"concepto": "total_pasivos", "valor": float(total_pasivos)},
                        {"concepto": "utilidad_neta", "valor": float(utilidad_neta)},
                    ],
                }
            ],
            "informacion_adicional": {
                "derivation_basis": [
                    "balance_general",
                    "estado_resultados",
                    "libro_auxiliar",
                ],
                "rule_version": "v1",
            },
        }

        ingest_job = db_service.create_ingest_job(
            db,
            file_name=f"derived_{normalized_nit}_{period_end.date().isoformat()}",
            file_path="internal://derived_financial_statements",
            commit=False,
        )
        ingest_job.status = IngestStatus.COMPLETED
        ingest_job.document_type = "derived_financial_statements"
        ingest_job.pathway = "work_with_existing"

        all_payloads = {
            "flujo_de_caja": flujo_data,
            "cambios_patrimonio": cambios_data,
            "notas_estados_financieros": notas_data,
        }
        created_rows = {}
        for statement_type, payload in [
            (t, all_payloads[t]) for t in targets_to_create
        ]:
            created_rows[statement_type] = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type=statement_type,
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived",
                data=payload,
                commit=False,
            )

        for target_type in targets_to_create:
            target = created_rows[target_type]
            for source_type in _REQUIRED_DERIVATION_INPUTS:
                source = source_rows[source_type]
                db_service.create_financial_statement_lineage(
                    db,
                    target_statement_id=target.id,
                    source_statement_id=source.id,
                    relation_type="input",
                    commit=False,
                )

        db.commit()

        return {
            "status": "derived",
            "company_nit": normalized_nit,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "created_statement_ids": {k: v.id for k, v in created_rows.items()},
            "source_statement_ids": {k: v.id for k, v in source_rows.items()},
            "derived_count": len(created_rows),
            "lineage_links": len(created_rows) * len(_REQUIRED_DERIVATION_INPUTS),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
