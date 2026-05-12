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


# ─── Derivation helpers (v3 — full NIC 7 / NIC 1) ─────────────────────────────


def _dec(value: Any) -> Decimal:
    """Best-effort Decimal cast. None / invalid → 0."""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _load_prior_balance(
    db: Session, company_nit: str, period_start: datetime
) -> "FinancialStatement | None":
    """Return the most recent direct balance_general with period_end < period_start.

    Used to compute working-capital variations and prior cash position required
    for the NIC 7 indirect method. Returning None signals the caller to raise a
    BusinessRuleError so the API surfaces a 409 instead of silently degrading.
    """
    from app.models.database import FinancialStatement  # local to avoid cycle

    return (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_nit == company_nit,
            FinancialStatement.statement_type == "balance_general",
            FinancialStatement.source_mode == "direct",
            FinancialStatement.period_end < period_start,
        )
        .order_by(FinancialStatement.period_end.desc())
        .first()
    )


def _sum_account_balances(
    accounts: list[dict[str, Any]] | None, *prefixes: str
) -> Decimal:
    """Sum the absolute `saldo` of accounts whose cuenta_puc starts with any prefix."""
    if not isinstance(accounts, list):
        return Decimal("0")
    total = Decimal("0")
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        code = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
        if any(code.startswith(p) for p in prefixes):
            total += _dec(acc.get("saldo") or acc.get("valor"))
    return total


def _nested(data: dict[str, Any] | None, *path: str) -> Decimal:
    """Walk a nested dict pulling Decimals. Missing keys → 0."""
    cur: Any = data or {}
    for key in path:
        if not isinstance(cur, dict):
            return Decimal("0")
        cur = cur.get(key)
        if cur is None:
            return Decimal("0")
    return _dec(cur)


def _compute_cash_flow_indirect(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    bg_data: dict[str, Any],
    prior_bg_data: dict[str, Any],
    er_data: dict[str, Any],
    la_data: dict[str, Any],
) -> dict[str, Any]:
    """Full NIC 7 indirect method using BG (current + prior) + ER + LA."""

    # ── Starting point: utilidad neta ──────────────────────────────────
    utilidad_neta = _dec(er_data.get("utilidad_neta"))

    # ── Add-back: depreciation (hybrid — BG diff preferred, ER accounts fallback) ──
    dep_acum_now = _sum_account_balances(bg_data.get("accounts"), "159")
    dep_acum_prior = _sum_account_balances(prior_bg_data.get("accounts"), "159")
    depreciacion_periodo: Decimal
    if dep_acum_now and dep_acum_prior is not None:
        depreciacion_periodo = abs(dep_acum_now - dep_acum_prior)
    else:
        depreciacion_periodo = _sum_account_balances(
            er_data.get("accounts"), "5160", "5260", "7560"
        )

    # ── Provisions (gasto-side, class 519x) ────────────────────────────
    provisiones = _sum_account_balances(er_data.get("accounts"), "519")

    # ── Working capital variations ─────────────────────────────────────
    cxc_now = _nested(bg_data, "activos_corrientes", "cuentas_por_cobrar_comerciales")
    cxc_prior = _nested(
        prior_bg_data, "activos_corrientes", "cuentas_por_cobrar_comerciales"
    )
    delta_cxc = cxc_now - cxc_prior

    inv_now = _nested(bg_data, "activos_corrientes", "inventarios")
    inv_prior = _nested(prior_bg_data, "activos_corrientes", "inventarios")
    delta_inv = inv_now - inv_prior

    cxp_now = _nested(bg_data, "pasivos_corrientes", "cuentas_por_pagar_comerciales")
    cxp_prior = _nested(
        prior_bg_data, "pasivos_corrientes", "cuentas_por_pagar_comerciales"
    )
    delta_cxp = cxp_now - cxp_prior

    obl_lab_now = _nested(bg_data, "pasivos_corrientes", "obligaciones_laborales")
    obl_lab_prior = _nested(
        prior_bg_data, "pasivos_corrientes", "obligaciones_laborales"
    )
    delta_obl_lab = obl_lab_now - obl_lab_prior

    impuesto_renta = _dec(er_data.get("impuesto_renta"))

    flujo_operacion = (
        utilidad_neta
        + depreciacion_periodo
        + provisiones
        - delta_cxc
        - delta_inv
        + delta_cxp
        + delta_obl_lab
        - impuesto_renta
    )

    # ── Investment: Δ PPE (class 15 net of depreciation) + Δ intangibles + Δ inv LP ──
    ppe_now = _nested(bg_data, "activos_no_corrientes", "propiedades_planta_equipo")
    ppe_prior = _nested(
        prior_bg_data, "activos_no_corrientes", "propiedades_planta_equipo"
    )
    intan_now = _nested(bg_data, "activos_no_corrientes", "intangibles")
    intan_prior = _nested(prior_bg_data, "activos_no_corrientes", "intangibles")
    inv_lp_now = _nested(bg_data, "activos_no_corrientes", "inversiones_largo_plazo")
    inv_lp_prior = _nested(
        prior_bg_data, "activos_no_corrientes", "inversiones_largo_plazo"
    )
    flujo_inversion = (
        -(ppe_now - ppe_prior)
        - (intan_now - intan_prior)
        - (inv_lp_now - inv_lp_prior)
    )

    # ── Financing: Δ obligaciones financieras + Δ capital + dividendos ──
    ob_fin_cp_now = _nested(bg_data, "pasivos_corrientes", "obligaciones_financieras_cp")
    ob_fin_cp_prior = _nested(
        prior_bg_data, "pasivos_corrientes", "obligaciones_financieras_cp"
    )
    ob_fin_lp_now = _nested(
        bg_data, "pasivos_no_corrientes", "obligaciones_financieras_lp"
    )
    ob_fin_lp_prior = _nested(
        prior_bg_data, "pasivos_no_corrientes", "obligaciones_financieras_lp"
    )
    capital_now = _nested(bg_data, "patrimonio", "capital_social")
    capital_prior = _nested(prior_bg_data, "patrimonio", "capital_social")

    # Dividendos pagados: LA lines with cuenta_puc startswith "3705" and debito>0
    dividendos = Decimal("0")
    la_lines = la_data.get("lines") or []
    if isinstance(la_lines, list):
        for line in la_lines:
            if not isinstance(line, dict):
                continue
            code = str(line.get("cuenta_puc") or "")
            if code.startswith("3705"):
                dividendos += _dec(line.get("debito"))

    flujo_financiacion = (
        (ob_fin_cp_now - ob_fin_cp_prior)
        + (ob_fin_lp_now - ob_fin_lp_prior)
        + (capital_now - capital_prior)
        - dividendos
    )

    # ── Cash positions ────────────────────────────────────────────────
    efectivo_fin = _nested(bg_data, "activos_corrientes", "efectivo_equivalentes")
    if efectivo_fin == 0:
        efectivo_fin = _sum_account_balances(bg_data.get("accounts"), "11")
    efectivo_inicio = _nested(
        prior_bg_data, "activos_corrientes", "efectivo_equivalentes"
    )
    if efectivo_inicio == 0:
        efectivo_inicio = _sum_account_balances(prior_bg_data.get("accounts"), "11")

    aumento_neto = flujo_operacion + flujo_inversion + flujo_financiacion
    expected_fin = efectivo_inicio + aumento_neto
    diferencia = efectivo_fin - expected_fin
    tolerance = max(abs(efectivo_fin) * Decimal("0.005"), Decimal("1"))
    verificacion = abs(diferencia) <= tolerance

    return {
        "tipo": "flujo_de_caja",
        "entidad": {"nit": company_nit},
        "periodo_inicio": period_start.date().isoformat(),
        "periodo_fin": period_end.date().isoformat(),
        "metodo": "indirecto",
        "base_presentacion": "NIIF_pymes",
        "moneda": "COP",
        "flujo_neto_operacion": float(flujo_operacion),
        "flujo_neto_inversion": float(flujo_inversion),
        "flujo_neto_financiacion": float(flujo_financiacion),
        "aumento_disminucion_neto": float(aumento_neto),
        "efectivo_inicio_periodo": float(efectivo_inicio),
        "efectivo_fin_periodo": float(efectivo_fin),
        "verificacion": verificacion,
        "informacion_adicional": {
            "derivation_basis": list(_REQUIRED_DERIVATION_INPUTS),
            "rule_version": "v3",
            "adjustments": {
                "utilidad_neta": float(utilidad_neta),
                "depreciacion_periodo": float(depreciacion_periodo),
                "provisiones": float(provisiones),
                "delta_cuentas_por_cobrar": float(delta_cxc),
                "delta_inventarios": float(delta_inv),
                "delta_cuentas_por_pagar": float(delta_cxp),
                "delta_obligaciones_laborales": float(delta_obl_lab),
                "impuesto_renta": float(impuesto_renta),
                "delta_ppe": float(ppe_now - ppe_prior),
                "delta_intangibles": float(intan_now - intan_prior),
                "delta_inversiones_largo_plazo": float(inv_lp_now - inv_lp_prior),
                "delta_obligaciones_financieras_cp": float(
                    ob_fin_cp_now - ob_fin_cp_prior
                ),
                "delta_obligaciones_financieras_lp": float(
                    ob_fin_lp_now - ob_fin_lp_prior
                ),
                "delta_capital_social": float(capital_now - capital_prior),
                "dividendos_pagados": float(dividendos),
            },
            "nic7_identity": {
                "expected_fin": float(expected_fin),
                "actual_fin": float(efectivo_fin),
                "diferencia": float(diferencia),
                "tolerance": float(tolerance),
            },
        },
    }


_EQUITY_COMPONENTS: tuple[tuple[str, str, str], ...] = (
    # (label_key,                 puc_prefix, nested_path_in_bg_patrimonio)
    ("capital_social",            "31",       "capital_social"),
    ("reservas",                  "33",       "reservas"),
    ("resultados_del_ejercicio",  "36",       "resultados_del_ejercicio"),
    ("resultados_acumulados",     "37",       "resultados_acumulados"),
    ("otro_resultado_integral",   "38",       "otro_resultado_integral"),
)


def _compute_equity_changes(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    bg_data: dict[str, Any],
    prior_bg_data: dict[str, Any],
    er_data: dict[str, Any],
    la_data: dict[str, Any],
) -> dict[str, Any]:
    """Build per-component equity changes (NIC 1 / Sección 6 NIIF Pymes)."""

    la_lines = la_data.get("lines") or []
    la_lines = la_lines if isinstance(la_lines, list) else []

    componentes: list[dict[str, Any]] = []
    utilidad_neta = _dec(er_data.get("utilidad_neta"))

    for label, prefix, nested_key in _EQUITY_COMPONENTS:
        saldo_final = _nested(bg_data, "patrimonio", nested_key)
        saldo_inicial = _nested(prior_bg_data, "patrimonio", nested_key)

        movimientos: list[dict[str, Any]] = []
        # Utilidad del ejercicio goes directly into resultados_del_ejercicio.
        if label == "resultados_del_ejercicio" and utilidad_neta != 0:
            movimientos.append(
                {"concepto": "utilidad_neta_del_periodo", "valor": float(utilidad_neta)}
            )

        # Pull LA line movements for this component.
        movs_debito = Decimal("0")
        movs_credito = Decimal("0")
        for line in la_lines:
            if not isinstance(line, dict):
                continue
            code = str(line.get("cuenta_puc") or "")
            if code.startswith(prefix):
                movs_debito += _dec(line.get("debito"))
                movs_credito += _dec(line.get("credito"))

        # Net movement: credits add to equity, debits subtract (patrimonio is
        # naturally credit-side).
        net = movs_credito - movs_debito
        if net != 0:
            movimientos.append(
                {
                    "concepto": "movimientos_libro_auxiliar",
                    "valor": float(net),
                    "debitos": float(movs_debito),
                    "creditos": float(movs_credito),
                }
            )

        # Skip the component entirely if both saldos are zero and there are no movements.
        if saldo_final == 0 and saldo_inicial == 0 and not movimientos:
            continue

        componentes.append(
            {
                "concepto_patrimonio": label,
                "saldo_inicial": float(saldo_inicial),
                "movimientos": movimientos,
                "saldo_final": float(saldo_final),
            }
        )

    total_patrimonio_inicio = sum(
        (_dec(c["saldo_inicial"]) for c in componentes), Decimal("0")
    )
    total_patrimonio_fin = sum(
        (_dec(c["saldo_final"]) for c in componentes), Decimal("0")
    )

    # Cross-check against BG.total_patrimonio
    bg_total_patrimonio = _dec(bg_data.get("total_patrimonio"))
    cuadre_patrimonio = (
        abs(total_patrimonio_fin - bg_total_patrimonio)
        <= max(abs(bg_total_patrimonio) * Decimal("0.005"), Decimal("1"))
    )

    return {
        "tipo": "cambios_patrimonio",
        "entidad": {"nit": company_nit},
        "periodo_inicio": period_start.date().isoformat(),
        "periodo_fin": period_end.date().isoformat(),
        "moneda": "COP",
        "componentes": componentes,
        "total_patrimonio_inicio": float(total_patrimonio_inicio),
        "total_patrimonio_fin": float(total_patrimonio_fin),
        "informacion_adicional": {
            "rule_version": "v3",
            "bg_total_patrimonio": float(bg_total_patrimonio),
            "cuadre_patrimonio": cuadre_patrimonio,
        },
    }


_NOTE_SPECS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # (numero, titulo, categoria, prefixes)
    ("4", "Efectivo y equivalentes de efectivo", "activo_corriente", ("11",)),
    ("5", "Deudores comerciales y otras cuentas por cobrar", "activo_corriente",
     ("13",)),
    ("6", "Propiedades, planta y equipo", "activo_no_corriente", ("15", "16", "17")),
    ("7", "Cuentas por pagar comerciales y otras", "pasivo_corriente", ("22", "23")),
    ("8", "Pasivos por impuestos corrientes", "pasivo_corriente", ("24",)),
    ("9", "Obligaciones laborales", "pasivo_corriente", ("25", "26")),
    ("10", "Patrimonio", "patrimonio", ("3",)),
    ("11", "Ingresos operacionales", "ingreso", ("41", "42")),
    ("12", "Gastos operacionales", "gasto", ("51", "52", "53")),
)


def _compute_notes(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    bg_data: dict[str, Any],
    er_data: dict[str, Any],
    la_data: dict[str, Any],
) -> dict[str, Any]:
    """Build a 12-note structure following NIC 1 minimums, skipping notes with no data."""

    notas: list[dict[str, Any]] = []
    entidad = bg_data.get("entidad") or {}
    marco_normativo = bg_data.get("marco_normativo") or er_data.get("marco_normativo") or "NIIF_pymes"

    # Nota 1: información general
    notas.append(
        {
            "numero_nota": "1",
            "titulo": "Información general de la entidad",
            "categoria": "informacion_general",
            "contenido_resumido": (
                f"NIT: {entidad.get('nit') or company_nit}. "
                f"Razón social: {entidad.get('razon_social') or 'No especificada'}."
            ),
            "cifras_relevantes": [],
        }
    )

    # Nota 2: bases de preparación
    notas.append(
        {
            "numero_nota": "2",
            "titulo": "Bases de preparación de los estados financieros",
            "categoria": "politicas_contables",
            "contenido_resumido": (
                f"Estados financieros preparados bajo {marco_normativo}. "
                "Moneda funcional y de presentación: pesos colombianos (COP). "
                "Período de reporte: "
                f"{period_start.date().isoformat()} a {period_end.date().isoformat()}."
            ),
            "cifras_relevantes": [],
        }
    )

    # Nota 3: políticas contables (template fijo)
    notas.append(
        {
            "numero_nota": "3",
            "titulo": "Políticas contables significativas",
            "categoria": "politicas_contables",
            "contenido_resumido": (
                "Reconocimiento de ingresos por devengo. Inventarios valorados al menor "
                "entre costo y valor neto realizable. PPE registrada al costo menos "
                "depreciación acumulada (método línea recta). Provisiones reconocidas "
                "cuando existe obligación presente derivada de un suceso pasado."
            ),
            "cifras_relevantes": [],
        }
    )

    # Notas 4-12: per-class breakdowns from accounts[]
    bg_accounts = bg_data.get("accounts") or []
    er_accounts = er_data.get("accounts") or []

    def _collect(accounts: list, prefixes: tuple[str, ...]) -> list[dict[str, Any]]:
        if not isinstance(accounts, list):
            return []
        out: list[dict[str, Any]] = []
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            code = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
            if any(code.startswith(p) for p in prefixes):
                out.append(
                    {
                        "concepto": (
                            f"{code} - {acc.get('nombre') or ''}".strip(" -")
                            if code
                            else acc.get("nombre") or ""
                        ),
                        "valor": float(_dec(acc.get("saldo") or acc.get("valor"))),
                    }
                )
        return out

    for numero, titulo, categoria, prefixes in _NOTE_SPECS:
        # Notes 4-10 come from BG, 11-12 from ER
        source = er_accounts if numero in ("11", "12") else bg_accounts
        cifras = _collect(source, prefixes)
        if not cifras:
            continue
        notas.append(
            {
                "numero_nota": numero,
                "titulo": titulo,
                "categoria": categoria,
                "contenido_resumido": (
                    f"Desglose por cuenta PUC para clases {', '.join(prefixes)}. "
                    f"Total de cuentas reportadas: {len(cifras)}."
                ),
                "cifras_relevantes": cifras,
            }
        )

    return {
        "tipo": "notas_estados_financieros",
        "entidad": {"nit": company_nit},
        "periodo_inicio": period_start.date().isoformat(),
        "periodo_fin": period_end.date().isoformat(),
        "base_presentacion": marco_normativo,
        "moneda_funcional": "COP",
        "hipotesis_negocio_en_marcha": True,
        "notas": notas,
        "informacion_adicional": {
            "derivation_basis": list(_REQUIRED_DERIVATION_INPUTS),
            "rule_version": "v3",
            "notas_count": len(notas),
        },
    }


def derive_financial_statements(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Derive cashflow/equity changes/notes from BG+ER+LA for one company and period.

    Raises:
        BusinessRuleError: when required source statements are not available, or
        when the prior-period balance_general (needed for NIC 7 indirect cash
        flow) is not present.
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

        # NIC 7 indirect method needs a prior-period BG to compute working-capital
        # variations and opening cash. Refuse rather than silently degrade.
        prior_bg_row = _load_prior_balance(db, normalized_nit, period_start)
        if prior_bg_row is None:
            raise BusinessRuleError(
                "Para derivar flujo de caja según NIC 7 método indirecto se requiere "
                "el balance general del período anterior. Sube el balance del período "
                "previo para esta empresa y vuelve a intentar."
            )
        prior_bg = prior_bg_row.data or {}

        flujo_data = _compute_cash_flow_indirect(
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
            bg_data=bg,
            prior_bg_data=prior_bg,
            er_data=er,
            la_data=la,
        )
        cambios_data = _compute_equity_changes(
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
            bg_data=bg,
            prior_bg_data=prior_bg,
            er_data=er,
            la_data=la,
        )
        notas_data = _compute_notes(
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
            bg_data=bg,
            er_data=er,
            la_data=la,
        )


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
