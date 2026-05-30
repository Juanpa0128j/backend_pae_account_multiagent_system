from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.auth import CurrentUser, get_current_user
from app.agents.graph import invoke_reporting_pipeline
from app.core.database import SessionLocal
from app.models.agent_outputs import BalanceSheetOutput, CashFlowOutput, PnLOutput
from app.models.database import FinancialStatement
from app.services.financial_statement_service import (
    BusinessRuleError,
    DERIVED_TARGETS as _DERIVED_TARGETS,
    build_first_level_from_journal_entries,
    derive_financial_statements,
    list_financial_statements,
)
from app.services.nit_utils import normalize_nit, normalize_optional_nit
from app.services.report_export_service import (
    BalanceSheetExporter,
    CashFlowExporter,
    PnLExporter,
    LibroDiarioExporter,
    LibroAuxiliarExporter,
    CambiosPatrimonioExporter,
    NotasEstadosFinancierosExporter,
)

router = APIRouter()


_REPORT_TYPE_ALIASES: dict[str, set[str]] = {
    "balance": {"balance", "balance_general"},
    "pnl": {"pnl", "estado_resultados"},
    "cashflow": {"cashflow", "flujo_de_caja"},
    "libro_diario": {"libro_diario"},
    "libro_auxiliar": {"libro_auxiliar"},
    "cambios_patrimonio": {"cambios_patrimonio"},
    "notas_eeff": {"notas_eeff", "notas_estados_financieros"},
}


def _build_params(
    start_date: Optional[date],
    resolved_end_date: Optional[date],
    include_analysis: bool = False,
) -> dict:
    params: dict = {}
    if start_date:
        params["start_date"] = start_date.isoformat()
    if resolved_end_date:
        params["end_date"] = resolved_end_date.isoformat()
    if include_analysis:
        params["include_analysis"] = True
    return params


_REPORT_TYPE_TO_STATEMENT_TYPE: dict[str, str] = {
    "balance": "balance_general",
    "pnl": "estado_resultados",
    "cashflow": "flujo_de_caja",
}


def _try_stored_statement(
    report_type: str, params: dict, normalized_company_nit: Optional[str]
) -> Optional[dict]:
    """Return the latest matching stored FinancialStatement as report data, or None.

    Vía B users upload statements directly; the journal-based reporting pipeline
    can't see them. Read the FinancialStatement table first and normalize to the
    exporter shape so /balance, /pnl, /cashflow work for both pathways.
    """
    if not normalized_company_nit:
        return None
    statement_type = _REPORT_TYPE_TO_STATEMENT_TYPE.get(report_type)
    if not statement_type:
        return None

    end_date_str = params.get("end_date")
    period_end = None
    if end_date_str:
        try:
            period_end = datetime.fromisoformat(end_date_str).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            period_end = None

    db = SessionLocal()
    try:
        # Para empresas Vía A (`build_from_scratch`) los FinancialStatement son
        # derived snapshots — duplican lo que el pipeline reportero recalcula
        # en vivo desde journal_entry_lines y suelen quedar stale al subir más
        # docs. Para Vía A: ignorar stored y dejar que el caller invoque el
        # pipeline fresh. Stored solo es la fuente de verdad para Vía B
        # (`work_with_existing` — uploads directos de estados financieros).
        try:
            from app.services import db_service as _db_svc  # noqa: PLC0415

            pathway = _db_svc.get_company_locked_pathway(db, normalized_company_nit)
        except Exception:
            pathway = None
        if pathway != "work_with_existing":
            return None

        q = db.query(FinancialStatement).filter(
            FinancialStatement.entity_nit == normalized_company_nit,
            FinancialStatement.statement_type == statement_type,
        )
        # Snapshot vigente para la fecha: periodo del statement debe haber
        # INICIADO antes/en el cutoff. Ordenar por created_at DESC para fresh.
        if period_end is not None:
            q = q.filter(FinancialStatement.period_start <= period_end)
        stmt = q.order_by(FinancialStatement.created_at.desc()).first()
        if stmt is None:
            return None
        return _normalize_stored_statement(report_type, stmt.data or {})
    finally:
        db.close()


def _run_report(report_type: str, params: dict, company_nit: Optional[str]) -> dict:
    """Invoke the reporting pipeline and raise HTTP 500 on agent error.

    For Vía B users, prefer the stored FinancialStatement when present — the
    journal-based pipeline can't see direct uploads.
    """
    normalized_company_nit = None
    if company_nit:
        try:
            normalized_company_nit = normalize_nit(company_nit)
        except ValueError as nit_err:
            raise HTTPException(
                status_code=422, detail=f"Invalid company_nit: {nit_err}"
            )

    stored = _try_stored_statement(report_type, params, normalized_company_nit)
    if stored is not None:
        # Provide the fields BalanceSheetOutput / PnLOutput / CashFlowOutput require
        # but that the stored shape doesn't include.
        from datetime import datetime as _dt

        stored.setdefault("report_type", report_type)
        stored.setdefault("company_nit", normalized_company_nit)
        stored.setdefault("generated_at", _dt.now(timezone.utc).isoformat())
        # setdefault does not replace an existing None value.
        if not stored.get("period_end"):
            stored["period_end"] = params.get("end_date") or date.today().isoformat()
        stored.setdefault("notas_normativas", [])
        return stored

    result = invoke_reporting_pipeline(
        report_type=report_type,
        report_params=params,
        company_nit=normalized_company_nit,
    )
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    report = result.get("report", {})
    # Defensive: response models require period_end as non-null string.
    if isinstance(report, dict) and not report.get("period_end"):
        report["period_end"] = params.get("end_date") or date.today().isoformat()
    return report


def _normalize_stored_statement(report_type: str, data: dict) -> dict:
    """Normalize stored FinancialStatement.data into the schema exporters expect.

    Stored statements use a different key/shape than the live pipeline output.
    This bridges the gap so exporters work identically for both sources.
    """

    def _to_float(v) -> float:
        if isinstance(v, dict):
            return 0.0
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    # balance_general / balance ────────────────────────────────────────────────
    if report_type in ("balance", "balance_general"):
        # Split per-account rows by PUC class so PDF/Excel exporters can render
        # detail tables instead of just the 3 aggregate totals.
        activos_detalle: list[dict] = []
        pasivos_detalle: list[dict] = []
        patrimonio_detalle: list[dict] = []

        raw_accounts = data.get("accounts") or data.get("cuentas") or []
        if isinstance(raw_accounts, list):
            for acc in raw_accounts:
                if not isinstance(acc, dict):
                    continue
                code = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
                if not code:
                    continue
                row = {
                    "codigo": code,
                    "nombre": acc.get("nombre") or "",
                    "saldo": _to_float(acc.get("saldo") or acc.get("valor")),
                }
                if code.startswith("1"):
                    activos_detalle.append(row)
                elif code.startswith("2"):
                    pasivos_detalle.append(row)
                elif code.startswith("3"):
                    patrimonio_detalle.append(row)

        # patrimonio (sin utilidad) is the clase-3 baseline that does NOT
        # include net profit. Exporters add `utilidad_neta` on top to get the
        # final patrimonio total. We must NOT fall back to `total_patrimonio`
        # here (which already includes utilidad): doing so double-counts the
        # net profit in the PDF/Excel footer. Use explicit None check because
        # 0 is a valid baseline (no equity movements yet).
        patrimonio_sin_utilidad_raw = data.get("patrimonio_sin_utilidad")
        if patrimonio_sin_utilidad_raw is None:
            total_patrimonio_raw = _to_float(data.get("total_patrimonio"))
            utilidad_neta_raw = _to_float(data.get("utilidad_neta"))
            patrimonio_value = total_patrimonio_raw - utilidad_neta_raw
        else:
            patrimonio_value = _to_float(patrimonio_sin_utilidad_raw)

        return {
            "period_start": data.get("periodo_inicio"),
            "period_end": data.get("periodo_fin") or data.get("period_end"),
            "activos": _to_float(data.get("total_activos")),
            "pasivos": _to_float(data.get("total_pasivos")),
            "patrimonio": patrimonio_value,
            "utilidad_neta": _to_float(data.get("utilidad_neta")),
            "patrimonio_total": _to_float(data.get("total_patrimonio")),
            "cuadre": bool(data.get("cuadre", False)),
            "mensaje_cuadre": "Balance derivado desde asientos contables.",
            "activos_detalle": activos_detalle,
            "pasivos_detalle": pasivos_detalle,
            "patrimonio_detalle": patrimonio_detalle,
        }

    # estado_resultados / pnl ──────────────────────────────────────────────────
    if report_type in ("pnl", "estado_resultados"):

        def _normalize_cuenta_list(items):
            out = []
            for c in items or []:
                if isinstance(c, dict):
                    out.append(
                        {
                            "codigo": c.get("cuenta_puc") or c.get("codigo") or "",
                            "nombre": c.get("nombre") or c.get("cuenta_puc") or "",
                            "saldo": _to_float(c.get("saldo") or c.get("valor")),
                        }
                    )
            return out

        ingresos = _normalize_cuenta_list(data.get("ingresos"))
        gastos = _normalize_cuenta_list(data.get("gastos"))
        costo_ventas = _normalize_cuenta_list(data.get("costo_ventas"))

        # Fallback: if the LLM only produced a flat `accounts` list, split by
        # PUC class (4 = ingresos, 5 = gastos, 6 = costo de ventas).
        if not (ingresos or gastos or costo_ventas):
            raw_accounts = data.get("accounts") or data.get("cuentas") or []
            if isinstance(raw_accounts, list):
                for acc in raw_accounts:
                    if not isinstance(acc, dict):
                        continue
                    code = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
                    row = {
                        "codigo": code,
                        "nombre": acc.get("nombre") or "",
                        "saldo": _to_float(acc.get("saldo") or acc.get("valor")),
                    }
                    if code.startswith("4"):
                        ingresos.append(row)
                    elif code.startswith("5"):
                        gastos.append(row)
                    elif code.startswith("6"):
                        costo_ventas.append(row)

        def _sum(items):
            return sum(_to_float(i.get("saldo")) for i in items)

        return {
            "period_start": data.get("periodo_inicio"),
            "period_end": data.get("periodo_fin") or data.get("period_end"),
            "ingresos": ingresos,
            "gastos": gastos,
            "costo_ventas": costo_ventas,
            "total_ingresos": _to_float(data.get("total_ingresos")) or _sum(ingresos),
            "total_gastos": _to_float(data.get("total_gastos")) or _sum(gastos),
            "total_costo_ventas": _to_float(data.get("total_costo_ventas"))
            or _sum(costo_ventas),
            "utilidad_bruta": _to_float(data.get("utilidad_bruta")),
            "utilidad_neta": _to_float(data.get("utilidad_neta")),
        }

    # cashflow / flujo_de_caja ─────────────────────────────────────────────────
    if report_type in ("cashflow", "flujo_de_caja"):
        efectivo_fin = _to_float(data.get("efectivo_fin_periodo"))
        efectivo_ini = _to_float(data.get("efectivo_inicio_periodo"))
        flujo_op = _to_float(data.get("flujo_neto_operacion"))
        flujo_inv = _to_float(data.get("flujo_neto_inversion"))
        flujo_fin_val = _to_float(data.get("flujo_neto_financiacion"))
        aumento_neto = _to_float(
            data.get("aumento_disminucion_neto")
            or (flujo_op + flujo_inv + flujo_fin_val)
        )
        info_adicional = data.get("informacion_adicional") or {}
        adjustments = info_adicional.get("adjustments") or {}
        nic7 = info_adicional.get("nic7_identity") or {}
        return {
            "period_start": data.get("periodo_inicio"),
            "period_end": data.get("periodo_fin") or data.get("period_end"),
            "metodo": data.get("metodo", "indirecto"),
            "verificacion": data.get("verificacion"),
            "efectivo_inicio": efectivo_ini,
            "flujo_operacion": flujo_op,
            "flujo_inversion": flujo_inv,
            "flujo_financiacion": flujo_fin_val,
            "aumento_disminucion_neto": aumento_neto,
            "efectivo_fin": efectivo_fin,
            "adjustments": adjustments,
            "nic7_diferencia": _to_float(nic7.get("diferencia")),
            "rule_version": info_adicional.get("rule_version", ""),
            # legacy keys kept for backward compat with old exporter paths
            "cuentas_efectivo": [
                {
                    "codigo": "11",
                    "nombre": "Efectivo y equivalentes",
                    "saldo": efectivo_fin,
                }
            ],
            "total_efectivo": efectivo_fin,
            "saldo_inicial": efectivo_ini,
            "nota": (
                f"Metodo indirecto. Flujo operacion: {flujo_op:,.0f} | "
                f"Inversion: {flujo_inv:,.0f} | Financiacion: {flujo_fin_val:,.0f}"
            ),
        }

    # libro_diario ─────────────────────────────────────────────────────────────
    if report_type == "libro_diario":
        return {
            "period_start": data.get("periodo_inicio"),
            "period_end": data.get("periodo_fin"),
            "asientos": data.get("asientos") or data.get("transacciones") or [],
        }

    # libro_auxiliar ───────────────────────────────────────────────────────────
    if report_type == "libro_auxiliar":
        raw_accounts = data.get("accounts") or data.get("cuentas") or []
        cuentas = []

        # Case 1: accounts/cuentas array (already grouped by account)
        if raw_accounts:
            for acc in raw_accounts:
                if not isinstance(acc, dict):
                    continue
                total_debito = _to_float(
                    acc.get("total_debito")
                    or acc.get("debito_total")
                    or acc.get("total_debit")
                )
                total_credito = _to_float(
                    acc.get("total_credito")
                    or acc.get("credito_total")
                    or acc.get("total_credit")
                )
                saldo = _to_float(
                    acc.get("saldo") or acc.get("saldo_neto") or acc.get("net_balance")
                )
                movimientos = acc.get("movimientos") or []
                if not movimientos and (total_debito or total_credito):
                    movimientos = [
                        {
                            "fecha": data.get("periodo_fin", ""),
                            "descripcion": "Saldo acumulado del periodo",
                            "debito": total_debito,
                            "credito": total_credito,
                        }
                    ]
                cuentas.append(
                    {
                        "cuenta": acc.get("cuenta_puc")
                        or acc.get("account")
                        or acc.get("cuenta")
                        or "",
                        "nombre": acc.get("nombre") or acc.get("name") or "",
                        "total_debito": total_debito,
                        "total_credito": total_credito,
                        "saldo": saldo,
                        "movimientos": movimientos,
                    }
                )
        else:
            # Case 2: AuxiliaryLedgerContent — flat lines[], group by cuenta_puc
            flat_lines = data.get("lines") or []
            grouped: dict = {}
            for line in flat_lines:
                if not isinstance(line, dict):
                    continue
                code = line.get("cuenta_puc") or "SIN_CUENTA"
                if code not in grouped:
                    grouped[code] = {
                        "cuenta": code,
                        "nombre": line.get("cuenta_nombre") or "",
                        "movimientos": [],
                        "total_debito": 0.0,
                        "total_credito": 0.0,
                        "saldo": 0.0,
                    }
                deb = _to_float(line.get("debito"))
                cred = _to_float(line.get("credito"))
                grouped[code]["movimientos"].append(
                    {
                        "fecha": line.get("fecha", ""),
                        "comprobante": line.get("comprobante", ""),
                        "descripcion": line.get("detalle")
                        or line.get("descripcion")
                        or "",
                        "debito": deb,
                        "credito": cred,
                    }
                )
                grouped[code]["total_debito"] += deb
                grouped[code]["total_credito"] += cred
                grouped[code]["saldo"] = (
                    grouped[code]["total_debito"] - grouped[code]["total_credito"]
                )
            cuentas = list(grouped.values())

        return {
            "period_start": data.get("periodo_inicio"),
            "period_end": data.get("periodo_fin"),
            "cuentas": cuentas,
        }

    # cambios_patrimonio ───────────────────────────────────────────────────────
    if report_type == "cambios_patrimonio":
        cambios = []
        for comp in data.get("componentes") or []:
            if not isinstance(comp, dict):
                continue
            movs = comp.get("movimientos") or []
            mov_debito = sum(
                _to_float(m.get("valor", 0))
                for m in movs
                if _to_float(m.get("valor", 0)) < 0
            )
            mov_credito = sum(
                _to_float(m.get("valor", 0))
                for m in movs
                if _to_float(m.get("valor", 0)) >= 0
            )
            cambios.append(
                {
                    "codigo": comp.get("concepto_patrimonio", ""),
                    "nombre": comp.get("concepto_patrimonio", "")
                    .replace("_", " ")
                    .title(),
                    "movimiento_debito": abs(mov_debito),
                    "movimiento_credito": mov_credito,
                    "saldo_final": _to_float(comp.get("saldo_final")),
                }
            )
        if not cambios:
            cambios = [
                {
                    "codigo": "3",
                    "nombre": "Patrimonio Total",
                    "movimiento_debito": 0,
                    "movimiento_credito": _to_float(data.get("total_patrimonio_fin")),
                    "saldo_final": _to_float(data.get("total_patrimonio_fin")),
                }
            ]
        return {
            "period_start": data.get("periodo_inicio"),
            "period_end": data.get("periodo_fin"),
            "cambios": cambios,
        }

    # notas_estados_financieros / notas_eeff ───────────────────────────────────
    if report_type in ("notas_eeff", "notas_estados_financieros"):
        notas_raw = data.get("notas") or []
        notas = []
        for n in notas_raw:
            if not isinstance(n, dict):
                continue
            notas.append(
                {
                    "numero": n.get("numero_nota") or n.get("numero") or 0,
                    "titulo": n.get("titulo") or "",
                    "contenido": n.get("contenido_resumido")
                    or n.get("contenido")
                    or "",
                    "categoria": n.get("categoria") or "",
                    "cifras_relevantes": n.get("cifras_relevantes") or [],
                }
            )
        cifras = {}
        for n in notas_raw:
            for c in n.get("cifras_relevantes") or []:
                cifras[c.get("concepto", "")] = _to_float(c.get("valor"))
        informacion_adicional = data.get("informacion_adicional") or {}
        activos = cifras.get(
            "total_activos",
            _to_float(informacion_adicional.get("activos")),
        )
        pasivos = cifras.get(
            "total_pasivos",
            _to_float(informacion_adicional.get("pasivos")),
        )
        patrimonio = _to_float(
            informacion_adicional.get("total_patrimonio")
            if informacion_adicional.get("total_patrimonio") is not None
            else informacion_adicional.get("patrimonio")
        )
        if patrimonio is None and activos is not None and pasivos is not None:
            patrimonio = activos - pasivos
        resumen = {
            "activos": activos,
            "pasivos": pasivos,
            "patrimonio": patrimonio if patrimonio is not None else 0,
        }
        return {
            "period_end": data.get("periodo_fin"),
            "notas": notas,
            "resumen_financiero": resumen,
        }

    # fallback: return as-is
    return data


def _resolve_report(
    report_type: str,
    statement_id: Optional[str],
    start_date: Optional[date],
    end_date: Optional[date],
    company_nit: Optional[str],
) -> tuple[dict, date]:
    """Return (report_data, resolved_end_date).

    If statement_id is given, load data directly from the stored FinancialStatement
    (same data the ojito preview shows) and normalize it to exporter schema.
    Otherwise re-run the pipeline.
    """
    resolved_end_date = end_date or date.today()

    if statement_id:
        if not company_nit:
            raise HTTPException(
                status_code=422,
                detail="El campo company_nit es obligatorio cuando se proporciona statement_id",
            )
        try:
            normalized_company_nit = normalize_nit(company_nit)
        except ValueError as nit_err:
            raise HTTPException(
                status_code=422, detail=f"Invalid company_nit: {nit_err}"
            )

        db = SessionLocal()
        try:
            stmt = (
                db.query(FinancialStatement)
                .filter(FinancialStatement.id == statement_id)
                .first()
            )
            if stmt is None:
                raise HTTPException(
                    status_code=404, detail=f"Statement {statement_id} not found"
                )

            expected_types = _REPORT_TYPE_ALIASES.get(report_type, {report_type})
            if stmt.statement_type not in expected_types:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Statement type mismatch: "
                        f"expected one of {sorted(expected_types)}, "
                        f"got {stmt.statement_type}"
                    ),
                )

            stmt_nit_normalized = None
            if stmt.entity_nit:
                try:
                    stmt_nit_normalized = normalize_nit(stmt.entity_nit)
                except ValueError:
                    stmt_nit_normalized = stmt.entity_nit.strip()

            if stmt_nit_normalized and stmt_nit_normalized != normalized_company_nit:
                raise HTTPException(
                    status_code=403,
                    detail="El estado financiero no pertenece al company_nit proporcionado",
                )

            raw = stmt.data or {}
            if stmt.period_end:
                resolved_end_date = stmt.period_end.date()
            normalized = _normalize_stored_statement(report_type, raw)
            return normalized, resolved_end_date
        finally:
            db.close()

    return (
        _run_report(
            report_type, _build_params(start_date, resolved_end_date), company_nit
        ),
        resolved_end_date,
    )


def _build_export_filename(
    base_name: str,
    extension: str,
    resolved_end_date: date,
    start_date: Optional[date] = None,
) -> str:
    """Build deterministic export filenames without empty date segments."""
    if start_date:
        return f"{base_name}_{start_date}_{resolved_end_date}.{extension}"
    return f"{base_name}_all_{resolved_end_date}.{extension}"


def _build_attachment_headers(
    base_name: str,
    extension: str,
    resolved_end_date: date,
    start_date: Optional[date] = None,
) -> dict[str, str]:
    filename = _build_export_filename(
        base_name, extension, resolved_end_date, start_date
    )
    return {"Content-Disposition": f"attachment; filename={filename}"}


@router.get("/balance", response_model=BalanceSheetOutput)
async def get_balance_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Balance General (Balance Sheet).
    Aggregates posted journal entries up to *end_date* grouped by PUC class.
    Returns assets, liabilities, equity, net profit and a balance-validation flag.
    """
    return _run_report("balance", _build_params(start_date, end_date), company_nit)


@router.get("/pnl", response_model=PnLOutput)
async def get_pnl_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Estado de Resultados (Profit & Loss).
    Aggregates revenue (class 4), COGS (class 6) and expenses (class 5)
    for the specified period. Optionally includes LLM-powered analysis.
    """
    return _run_report("pnl", _build_params(start_date, end_date), company_nit)


@router.get("/cashflow", response_model=CashFlowOutput)
async def get_cashflow_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Flujo de Caja (Cash Flow — direct method).
    Returns net balances of cash and bank accounts (class 11XX) for the period.
    Optionally includes LLM-powered analysis.
    """
    return _run_report("cashflow", _build_params(start_date, end_date), company_nit)


# ---------------------------------------------------------------------------
# JSON GET endpoints for report types previously available only as PDF/Excel
# downloads. Return raw builder dicts (no strict Pydantic schema) so the
# frontend can render interactive views (tables, drill-downs) without
# downloading a file.
# ---------------------------------------------------------------------------


@router.get("/libro_diario")
async def get_libro_diario_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD"),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Libro Diario (Daily Journal) — chronological list of every journal entry
    line for the period. Used by the frontend to render the Libros tab without
    downloading a PDF first.
    """
    return _run_report("libro_diario", _build_params(start_date, end_date), company_nit)


@router.get("/libro_auxiliar")
async def get_libro_auxiliar_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD"),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Libro Auxiliar (Subsidiary Ledger) — journal entries grouped by cuenta_puc
    with cumulative debit/credit totals per account.
    """
    return _run_report(
        "libro_auxiliar", _build_params(start_date, end_date), company_nit
    )


@router.get("/cambios_patrimonio")
async def get_cambios_patrimonio_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD"),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Estado de Cambios en el Patrimonio (Statement of Changes in Equity).
    Movements of class-3 accounts (capital, reserves, retained earnings) for
    the period.
    """
    return _run_report(
        "cambios_patrimonio", _build_params(start_date, end_date), company_nit
    )


@router.get("/notas_estados_financieros")
async def get_notas_eeff_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD"),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Notas a los Estados Financieros — RAG-backed normative references plus a
    balance summary. Used for compliance disclosures.
    """
    return _run_report("notas_eeff", _build_params(start_date, end_date), company_nit)


@router.get("/analysis")
async def get_analysis_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD"),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Comprehensive Financial Analysis — 7 ratios, top accounts, anomaly
    detection, 3-month predictions, LLM-narrated executive summary. Previously
    only reachable via the chat `intent=analysis` flow; now exposed as REST
    so the frontend Reportes tab can render it directly.
    """
    return _run_report("analysis", _build_params(start_date, end_date), company_nit)


@router.get("/statements")
async def get_financial_statements(
    company_nit: str = Query(..., description="Company NIT"),
    statement_type: Optional[str] = Query(
        None, description="Filter by type (e.g. flujo_de_caja)"
    ),
    start_date: Optional[date] = Query(None, description="Period start YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="Period end YYYY-MM-DD"),
    source_mode: Optional[str] = Query(
        None, description="Filter: direct | derived | derived_from_journal"
    ),
    limit: int = Query(
        100, ge=1, le=500, description="Max records to return (1-500, default 100)"
    ),
    offset: int = Query(0, ge=0, description="Records to skip (for pagination)"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List stored FinancialStatement records for a company.

    Pagination caps the response so a tenant with thousands of derived
    statements doesn't blow up the payload. Use ``limit`` + ``offset`` or
    progressively narrower date ranges.
    """
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    period_start = (
        datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
        if start_date
        else None
    )
    period_end = (
        datetime(
            end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc
        )
        if end_date
        else None
    )

    rows = list_financial_statements(
        company_nit=normalized_nit,
        period_start=period_start,
        period_end=period_end,
        statement_type=statement_type,
        source_mode=source_mode,
    )
    return rows[offset : offset + limit]


@router.get("/statements/{statement_id}")
async def get_financial_statement_by_id(
    statement_id: str,
    company_nit: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a specific FinancialStatement by ID."""
    db = SessionLocal()
    try:
        stmt = (
            db.query(FinancialStatement)
            .filter(FinancialStatement.id == statement_id)
            .first()
        )
        if stmt is None:
            raise HTTPException(
                status_code=404, detail=f"Statement {statement_id} not found"
            )
        if company_nit is not None and stmt.company_nit != normalize_optional_nit(
            company_nit
        ):
            raise HTTPException(status_code=403, detail="Acceso denegado")
        return {
            "id": stmt.id,
            "ingest_id": stmt.ingest_id,
            "statement_type": stmt.statement_type,
            "period_start": (
                stmt.period_start.isoformat() if stmt.period_start else None
            ),
            "period_end": stmt.period_end.isoformat() if stmt.period_end else None,
            "entity_nit": stmt.entity_nit,
            "source_mode": stmt.source_mode,
            "data": stmt.data,
            "created_at": stmt.created_at.isoformat() if stmt.created_at else None,
        }
    finally:
        db.close()


# ============================================================================
# Export Endpoints: Download reports in PDF and Excel formats
# ============================================================================


@router.get("/balance/download/pdf")
async def download_balance_pdf(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for PDF header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Balance Sheet as PDF."""
    report, resolved_end_date = _resolve_report(
        "balance", statement_id, start_date, end_date, company_nit
    )

    try:
        pdf_bytes = BalanceSheetExporter.to_pdf(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers=_build_attachment_headers(
            "balance_general", "pdf", resolved_end_date, start_date
        ),
    )


@router.get("/balance/download/excel")
async def download_balance_excel(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for Excel header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Balance Sheet as Excel."""
    report, resolved_end_date = _resolve_report(
        "balance", statement_id, start_date, end_date, company_nit
    )

    try:
        excel_bytes = BalanceSheetExporter.to_excel(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_build_attachment_headers(
            "balance_general", "xlsx", resolved_end_date, start_date
        ),
    )


@router.get("/pnl/download/pdf")
async def download_pnl_pdf(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for PDF header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Profit & Loss as PDF."""
    report, resolved_end_date = _resolve_report(
        "pnl", statement_id, start_date, end_date, company_nit
    )

    try:
        pdf_bytes = PnLExporter.to_pdf(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers=_build_attachment_headers(
            "estado_resultados", "pdf", resolved_end_date, start_date
        ),
    )


@router.get("/pnl/download/excel")
async def download_pnl_excel(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
    company_name: str = Query("Empresa", description="Company name for Excel header"),
):
    """Download Profit & Loss as Excel."""
    report, resolved_end_date = _resolve_report(
        "pnl", statement_id, start_date, end_date, company_nit
    )

    try:
        excel_bytes = PnLExporter.to_excel(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_build_attachment_headers(
            "estado_resultados", "xlsx", resolved_end_date, start_date
        ),
    )


@router.get("/cashflow/download/pdf")
async def download_cashflow_pdf(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for PDF header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Cash Flow as PDF."""
    report, resolved_end_date = _resolve_report(
        "cashflow", statement_id, start_date, end_date, company_nit
    )

    try:
        pdf_bytes = CashFlowExporter.to_pdf(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers=_build_attachment_headers(
            "flujo_caja", "pdf", resolved_end_date, start_date
        ),
    )


@router.get("/cashflow/download/excel")
async def download_cashflow_excel(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for Excel header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Cash Flow as Excel."""
    report, resolved_end_date = _resolve_report(
        "cashflow", statement_id, start_date, end_date, company_nit
    )

    try:
        excel_bytes = CashFlowExporter.to_excel(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_build_attachment_headers(
            "flujo_caja", "xlsx", resolved_end_date, start_date
        ),
    )


@router.get("/libro_diario/download/pdf")
async def download_libro_diario_pdf(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for PDF header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Libro Diario as PDF."""
    report, resolved_end_date = _resolve_report(
        "libro_diario", statement_id, start_date, end_date, company_nit
    )

    try:
        pdf_bytes = LibroDiarioExporter.to_pdf(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers=_build_attachment_headers(
            "libro_diario", "pdf", resolved_end_date, start_date
        ),
    )


@router.get("/libro_diario/download/excel")
async def download_libro_diario_excel(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for Excel header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Libro Diario as Excel."""
    report, resolved_end_date = _resolve_report(
        "libro_diario", statement_id, start_date, end_date, company_nit
    )

    try:
        excel_bytes = LibroDiarioExporter.to_excel(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_build_attachment_headers(
            "libro_diario", "xlsx", resolved_end_date, start_date
        ),
    )


@router.get("/libro_auxiliar/download/pdf")
async def download_libro_auxiliar_pdf(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for PDF header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Libro Auxiliar as PDF."""
    report, resolved_end_date = _resolve_report(
        "libro_auxiliar", statement_id, start_date, end_date, company_nit
    )

    try:
        pdf_bytes = LibroAuxiliarExporter.to_pdf(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers=_build_attachment_headers(
            "libro_auxiliar", "pdf", resolved_end_date, start_date
        ),
    )


@router.get("/libro_auxiliar/download/excel")
async def download_libro_auxiliar_excel(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
    company_name: str = Query("Empresa", description="Company name for Excel header"),
):
    """Download Libro Auxiliar as Excel."""
    report, resolved_end_date = _resolve_report(
        "libro_auxiliar", statement_id, start_date, end_date, company_nit
    )

    try:
        excel_bytes = LibroAuxiliarExporter.to_excel(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_build_attachment_headers(
            "libro_auxiliar", "xlsx", resolved_end_date, start_date
        ),
    )


@router.get("/cambios_patrimonio/download/pdf")
async def download_cambios_patrimonio_pdf(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for PDF header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Cambios en el Patrimonio as PDF."""
    report, resolved_end_date = _resolve_report(
        "cambios_patrimonio", statement_id, start_date, end_date, company_nit
    )

    try:
        pdf_bytes = CambiosPatrimonioExporter.to_pdf(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers=_build_attachment_headers(
            "cambios_patrimonio", "pdf", resolved_end_date, start_date
        ),
    )


@router.get("/cambios_patrimonio/download/excel")
async def download_cambios_patrimonio_excel(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for Excel header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Cambios en el Patrimonio as Excel."""
    report, resolved_end_date = _resolve_report(
        "cambios_patrimonio", statement_id, start_date, end_date, company_nit
    )

    try:
        excel_bytes = CambiosPatrimonioExporter.to_excel(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_build_attachment_headers(
            "cambios_patrimonio", "xlsx", resolved_end_date, start_date
        ),
    )


@router.get("/notas_estados_financieros/download/pdf")
async def download_notas_pdf(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for PDF header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Notas a los Estados Financieros as PDF."""
    report, resolved_end_date = _resolve_report(
        "notas_eeff", statement_id, start_date, end_date, company_nit
    )

    try:
        pdf_bytes = NotasEstadosFinancierosExporter.to_pdf(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers=_build_attachment_headers(
            "notas_estados_financieros", "pdf", resolved_end_date, start_date
        ),
    )


@router.get("/notas_estados_financieros/download/excel")
async def download_notas_excel(
    statement_id: Optional[str] = Query(
        None, description="Load from stored statement (same data as ojito)"
    ),
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    company_name: str = Query("Empresa", description="Company name for Excel header"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download Notas a los Estados Financieros as Excel."""
    report, resolved_end_date = _resolve_report(
        "notas_eeff", statement_id, start_date, end_date, company_nit
    )

    try:
        excel_bytes = NotasEstadosFinancierosExporter.to_excel(report, company_name)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Export failed: {str(e)}")

    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_build_attachment_headers(
            "notas_estados_financieros", "xlsx", resolved_end_date, start_date
        ),
    )


# ─── Vía B Manual Derivation ──────────────────────────────────────────────────

_REQUIRED_SOURCE_TYPES = ("balance_general", "estado_resultados", "libro_auxiliar")


@router.get("/derivation/status")
async def get_derivation_status(
    company_nit: str = Query(..., description="Company NIT"),
):
    """Report which Vía B source statements are uploaded for a company.

    Returns one entry per source type with its periods (if any), plus a
    `ready_periods` list showing which `(period_start, period_end)` windows
    have all 3 source statements present and would therefore allow derivation.
    """
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    sources: dict[str, list[dict]] = {t: [] for t in _REQUIRED_SOURCE_TYPES}
    rows = list_financial_statements(
        company_nit=normalized_nit,
        statement_type=None,
        source_mode="direct",
    )
    for row in rows:
        st = row.get("statement_type")
        if st in sources:
            sources[st].append(
                {
                    "id": row.get("id"),
                    "period_start": row.get("period_start"),
                    "period_end": row.get("period_end"),
                }
            )

    # A period is "ready" if all 3 source types have a statement covering it.
    # Use period_end as the matching key (a balance is a snapshot at period_end).
    period_end_sets: dict[str, set] = {
        t: {item["period_end"] for item in sources[t] if item["period_end"]}
        for t in _REQUIRED_SOURCE_TYPES
    }
    common_period_ends = (
        set.intersection(*period_end_sets.values())
        if period_end_sets.values()
        else set()
    )

    ready_periods = []
    for pe in sorted(common_period_ends, reverse=True):
        # For each common period_end, find the matching period_start of estado_resultados (rango)
        # Balance is snapshot, so it uses period_end as both. Use ER's range as the canonical period.
        er_match = next(
            (item for item in sources["estado_resultados"] if item["period_end"] == pe),
            None,
        )
        if er_match:
            ready_periods.append(
                {
                    "period_start": er_match["period_start"],
                    "period_end": pe,
                }
            )

    # Derived statements already produced for this company (source_mode="derived" only)
    all_rows = list_financial_statements(
        company_nit=normalized_nit, statement_type=None, source_mode="derived"
    )
    derived_by_pe: dict[str, list[str]] = {}
    for row in all_rows:
        st = row.get("statement_type")
        if st in _DERIVED_TARGETS:
            pe = row.get("period_end")
            if pe:
                derived_by_pe.setdefault(pe, []).append(st)
    derived_periods = sorted(
        [
            {
                "period_end": pe,
                "statements": types,
                "complete": set(types) >= set(_DERIVED_TARGETS),
            }
            for pe, types in derived_by_pe.items()
        ],
        key=lambda x: x["period_end"],
        reverse=True,
    )

    return {
        "company_nit": normalized_nit,
        "sources": sources,
        "ready_periods": ready_periods,
        "derived_periods": derived_periods,
        "is_ready": len(ready_periods) > 0,
    }


@router.post("/derivation/run")
async def run_derivation(
    company_nit: str = Query(..., description="Company NIT"),
    start_date: date = Query(..., description="Period start YYYY-MM-DD"),
    end_date: date = Query(..., description="Period end YYYY-MM-DD"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Manually trigger Vía B derivation for the given company and period."""
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    period_start = datetime(
        start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc
    )
    period_end = datetime(
        end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc
    )

    try:
        result = derive_financial_statements(
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
        )
    except BusinessRuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {"status": "ok", "result": result}


@router.post("/derivation/run-via-a")
async def run_derivation_via_a(
    company_nit: str = Query(..., description="Company NIT"),
    start_date: date = Query(..., description="Period start YYYY-MM-DD"),
    end_date: date = Query(..., description="Period end YYYY-MM-DD"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Manually trigger Vía A derivation: build first-level statements from journal
    entries, then derive flujo de caja / cambios patrimonio / notas.  Both steps are
    idempotent — already-existing statements are skipped."""
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    period_start = datetime(
        start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc
    )
    period_end = datetime(
        end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc
    )

    db = SessionLocal()
    try:
        first_level = build_first_level_from_journal_entries(
            db,
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
        )
        db.commit()
    finally:
        db.close()

    # If any required first-level state failed to build, surface the error now
    # instead of letting derive_financial_statements raise a cryptic 409.
    required = {"balance_general", "estado_resultados", "libro_auxiliar"}
    build_errors = first_level.get("build_errors", {})
    # A type is acceptable if it was created now OR was already in the DB (skipped).
    # Only fail if it ended up in build_errors (truly failed, not just pre-existing).
    failed_required = {t: e for t, e in build_errors.items() if t in required}
    if failed_required:
        detail = (
            "No se pudieron construir los estados de primer nivel desde los asientos. "
            "Verifica que las transacciones del periodo estén procesadas y persistidas. "
            f"Errores: { {k: v[:200] for k, v in failed_required.items()} }"
        )
        raise HTTPException(status_code=409, detail=detail)

    try:
        derived = derive_financial_statements(
            company_nit=normalized_nit,
            period_start=period_start,
            period_end=period_end,
            allow_missing_prior=True,  # Via A: first period has no prior BG
        )
    except BusinessRuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Warn if there are journal entries before this period that were never
    # derived — the cash flow opening balance will be zero instead of the real
    # prior balance, producing incorrect working-capital deltas.
    prior_warning: str | None = None
    db2 = SessionLocal()
    try:
        from app.models.database import JournalEntryLine as _JEL  # noqa: PLC0415
        from app.models.database import TransactionPosted as _TP  # noqa: PLC0415
        from app.models.database import TransactionStatus as _TS  # noqa: PLC0415

        has_prior_journal = (
            db2.query(_JEL)
            .join(_TP, _JEL.transaction_posted_id == _TP.id)
            .filter(
                _JEL.company_nit == normalized_nit,
                _JEL.fecha < period_start,
                _TP.status == _TS.POSTED,
            )
            .first()
            is not None
        )
        if has_prior_journal:
            from app.services.financial_statement_service import (
                _load_prior_balance,
            )  # noqa: PLC0415

            prior_bg = _load_prior_balance(
                db2, normalized_nit, period_start, via_a=True
            )
            if prior_bg is None:
                prior_warning = (
                    "Existen asientos de períodos anteriores que aún no han sido derivados. "
                    "El flujo de caja de este período usa saldo inicial $0 en lugar del "
                    "balance real del período previo. Deriva los períodos anteriores primero "
                    "y vuelve a derivar este período para corregirlo."
                )
    finally:
        db2.close()

    return {
        "status": "ok",
        "first_level": first_level,
        "derived": derived,
        **({"prior_period_warning": prior_warning} if prior_warning else {}),
    }


@router.get("/derivation/status-via-a")
async def get_derivation_status_via_a(
    company_nit: str = Query(..., description="Company NIT"),
):
    """Return derivation status for Vía A companies: first-level statements derived
    from journal entries and secondary derivations (flujo de caja, etc.)."""
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    all_rows = list_financial_statements(
        company_nit=normalized_nit,
        statement_type=None,
        source_mode=None,
    )

    # Group first-level statements (source_mode="derived_from_journal") by period
    first_level_by_period: dict[str, dict] = {}
    derived_by_period_end: dict[str, list[str]] = {}
    earliest: str | None = None
    latest: str | None = None

    for row in all_rows:
        st = row.get("statement_type")
        pe = row.get("period_end")
        ps = row.get("period_start")

        if pe and row.get("source_mode") == "derived_from_journal":
            key = f"{ps}|{pe}"
            if key not in first_level_by_period:
                first_level_by_period[key] = {
                    "period_start": ps,
                    "period_end": pe,
                    "types": [],
                }
            first_level_by_period[key]["types"].append(st)
            if earliest is None or (ps and ps < earliest):
                earliest = ps
            if latest is None or (pe and pe > latest):
                latest = pe

        if st in _DERIVED_TARGETS and pe and row.get("source_mode") == "derived":
            derived_by_period_end.setdefault(pe, []).append(st)

    derived_periods = sorted(
        [
            {
                "period_end": pe,
                "statements": types,
                "complete": set(types) >= set(_DERIVED_TARGETS),
            }
            for pe, types in derived_by_period_end.items()
        ],
        key=lambda x: x["period_end"],
        reverse=True,
    )

    # Detect order gaps: a period has a gap when its period_start is not the
    # global earliest AND no derived BG exists with period_end < period_start.
    # This means the prior period was never derived, so NIC 7 cash flow will
    # use empty opening balances and produce incorrect working-capital deltas.
    derived_bg_ends: set[str] = set()
    for row in all_rows:
        if (
            row.get("statement_type") == "balance_general"
            and row.get("source_mode") in ("derived_from_journal", "derived", "direct")
            and row.get("period_end")
        ):
            derived_bg_ends.add(row["period_end"][:10])

    sorted_periods = sorted(
        first_level_by_period.values(), key=lambda x: x["period_end"], reverse=True
    )
    for p in sorted_periods:
        ps = (p.get("period_start") or "")[:10]
        # First period ever → no prior expected
        if not ps or ps == (earliest or "")[:10]:
            p["prior_period_gap"] = False
            continue
        # Has any derived BG with period_end before this period_start?
        has_prior = any(pe < ps for pe in derived_bg_ends)
        p["prior_period_gap"] = not has_prior

    return {
        "company_nit": normalized_nit,
        "first_level_periods": sorted_periods,
        "derived_periods": derived_periods,
        "journal_date_range": {"earliest": earliest, "latest": latest},
    }
