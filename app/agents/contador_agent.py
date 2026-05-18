"""
Contador (Accountant) worker node for the process graph.

Receives staged raw transactions from state, queries the RAG service for
relevant PUC codes/normativa, and uses the LLM to produce a balanced
ContadorOutput (partida doble) following Colombian PUC standards.

On retry (when correction_feedback is present), the previous invalid
output and the schema errors are re-sent to the LLM for self-correction.

Document-specific rules:
- `recibo_caja`: Uses tipo_recibo signal to choose credit account:
  * 'cobro_cartera' → 130505 (cuentas por cobrar)
  * 'venta_directa' → 4xxx (ingresos según actividad)
  * absent/other → 130505 (default: cobro de cartera)
  See app/core/prompts/contador.py for full rule.
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.contador_puc_corrector import correct_contador_output
from app.agents.llm_retry import (
    is_double_entry_violation,
    is_invalid_puc,
    is_parse_error,
    llm_with_parse_retry,
)
from app.agents.state import AgentState
from app.core.llm_client import get_llm_client

logger = logging.getLogger(__name__)


# Frontend DocumentType → ContadorOutput.tipo_documento Pydantic enum.
# The frontend HITL UI sends granular values (factura_venta vs factura_compra)
# but the contador schema only accepts the coarser set defined in
# app/models/llm_schemas.py. Without normalization the LLM echoes the input
# value verbatim and Pydantic rejects it (SCHEMA_VALIDATION_EXHAUSTED).
_DOC_TYPE_TO_CONTADOR_ENUM = {
    "factura_venta": "factura",
    "factura_compra": "factura",
    "comprobante_egreso": "comprobante_egreso",
    "documento_soporte": "factura",
    "cuenta_cobro": "factura",
    "extracto_bancario": "extracto",
    "conciliacion_bancaria": "extracto",
    "recibo_caja": "recibo",
    "recibo_pago_impuesto": "recibo",
    "nota_credito": "nota_credito",
    "nota_debito": "nota_debito",
    "declaracion_iva": "otro",
    "declaracion_ica": "otro",
    "autorretencion_ica": "otro",
    "anexo_iva": "otro",
    "auxiliar_iva": "otro",
    "nomina": "nomina",
    "liquidacion_cesantias": "liquidacion_cesantias",
    "planilla_seguridad_social": "otro",
}


def _normalize_doc_type_for_schema(doc_type: str) -> str:
    return _DOC_TYPE_TO_CONTADOR_ENUM.get(doc_type, "otro")


def _extract_source_taxes_summary(source_doc: dict) -> dict | None:
    """Extract a tax summary dict from a structured ingest extraction document.

    Returns None when no tax-relevant fields are found (so callers can skip
    the optional prompt section entirely).
    """
    result: dict = {}

    # Top-level totals (FacturaVentaContent / FacturaCompraContent use a nested `totales` dict)
    totales = source_doc.get("totales") or {}
    if isinstance(totales, dict):
        for key in (
            "total_iva",
            "total_retenciones",
            "total_inc",
            "total_otros_impuestos",
        ):
            val = totales.get(key)
            if val is not None:
                result[key] = float(val)

    # Flat top-level IVA fields (some schemas put it at root)
    for key in ("total_iva", "total_nota_credito", "total_nota_debito"):
        if key not in result and source_doc.get(key) is not None:
            result[key] = float(source_doc[key])

    # Retenciones aplicadas (list of {tipo, base, tarifa, valor})
    retenciones = source_doc.get("retenciones_aplicadas") or []
    if isinstance(retenciones, list) and retenciones:

        def _safe_float(val: object, default: float = 0.0) -> float:
            try:
                return float(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        result["retenciones_aplicadas"] = [
            {
                "tipo": str(r.get("tipo", "")),
                "base": _safe_float(r.get("base")),
                "tarifa": _safe_float(r.get("tarifa")),
                "valor": _safe_float(r.get("valor")),
            }
            for r in retenciones
            if isinstance(r, dict)
        ]

    # Item-level tax flags — summarise gravado/excluido/exento counts
    items = source_doc.get("items") or []
    if isinstance(items, list) and items:
        gravado = sum(
            1 for it in items if isinstance(it, dict) and it.get("es_gravado")
        )
        excluido = sum(
            1 for it in items if isinstance(it, dict) and it.get("es_excluido")
        )
        exento = sum(1 for it in items if isinstance(it, dict) and it.get("es_exento"))
        if gravado or excluido or exento:
            result["items_gravado"] = gravado
            result["items_excluido"] = excluido
            result["items_exento"] = exento
            # Base gravable = sum of totals for gravado items only
            base_items = sum(
                float(it.get("valor_total_sin_impuesto") or it.get("valor_total") or 0)
                for it in items
                if isinstance(it, dict) and it.get("es_gravado")
            )
            if base_items > 0:
                result["base_gravable_from_items"] = base_items

    return result if result else None


_VENTA_DOC_TYPES = frozenset(
    {
        "factura_venta",
        "nota_debito_venta",
        "nota_credito_venta",
        "recibo_caja",
    }
)


def _extract_prearmed_asientos(source_doc: dict) -> list[dict] | None:
    """Return the journal-entry lines already printed on the source document.

    Some Colombian comprobantes (CE, RC, payroll vouchers, manual journals)
    carry a fully booked entry table with PUC code + debit + credit per line.
    When the ingest pipeline extracts those into ``source_doc.asientos_documento``
    we should respect them verbatim rather than re-running the LLM contador.

    Returns ``None`` when:
      - the field is missing or empty,
      - no line has a non-zero debit or credit,
      - the lines do NOT balance (sum debits != sum credits) — in that case the
        LLM path is safer than producing an unbalanced asiento.
    """
    raw_lines = (source_doc or {}).get("asientos_documento")
    if not isinstance(raw_lines, list) or not raw_lines:
        return None

    from decimal import Decimal as _Decimal

    normalized: list[dict] = []
    sum_debitos = _Decimal("0")
    sum_creditos = _Decimal("0")
    for line in raw_lines:
        if not isinstance(line, dict):
            continue
        codigo = str(line.get("codigo_cuenta") or "").strip()
        if not codigo:
            continue
        try:
            debito = _Decimal(str(line.get("debito") or 0))
            credito = _Decimal(str(line.get("credito") or 0))
        except Exception:
            return None
        if debito < 0 or credito < 0:
            return None
        valor = debito if debito > 0 else credito
        if valor == 0:
            continue
        tipo = "debito" if debito > 0 else "credito"
        normalized.append(
            {
                "cuenta_puc": codigo,
                "tipo_movimiento": tipo,
                "valor": str(valor),
                "tercero_nit": str(line.get("tercero") or ""),
                "descripcion": str(
                    line.get("concepto") or line.get("descripcion") or ""
                ),
                "nombre_cuenta": str(line.get("concepto") or ""),
            }
        )
        sum_debitos += debito
        sum_creditos += credito

    if not normalized:
        return None
    if sum_debitos != sum_creditos:
        logger.warning(
            "contador: source_doc.asientos_documento unbalanced (D=%s, C=%s); "
            "falling back to LLM",
            sum_debitos,
            sum_creditos,
        )
        return None
    return normalized


def _load_company_context(company_nit: str | None) -> dict | None:
    """Return the emisor (tenant) ``company_settings`` row as a dict.

    Used to enrich the contador prompt with the empresa's actividad económica
    (CIIU), nombre, ciudad and IVA-responsable flag so the LLM can pick a
    4xxx ingreso account that actually matches the business.

    Returns ``None`` if NIT is empty, the row is missing, or the DB layer
    raises — the prompt is still buildable without this context.
    """
    if not company_nit:
        return None
    try:
        from app.core.database import SessionLocal
        from app.services import db_service as _db_svc
    except Exception as imp_err:
        logger.warning("contador: company context import failed: %s", imp_err)
        return None

    db = None
    try:
        db = SessionLocal()
        row = _db_svc.get_company_settings(db, company_nit)
        if row is None:
            return None
        return {
            "nit": getattr(row, "nit", company_nit),
            "nombre": getattr(row, "nombre", None),
            "ciudad": getattr(row, "ciudad", None),
            "codigo_ciiu": getattr(row, "codigo_ciiu", None),
            "iva_responsable": bool(getattr(row, "iva_responsable", False)),
        }
    except Exception as db_err:
        logger.warning("contador: company context lookup failed: %s", db_err)
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _load_puc_ingresos_catalog() -> list[dict]:
    """Return PUC accounts in the ingresos range (4xxx) as ``{codigo, descripcion}``.

    Empty list when the catalog table is unreachable so the prompt still builds.
    The LLM treats the list as a soft constraint via the prompt directive.
    """
    try:
        from app.core.database import SessionLocal
        from app.models.database import CuentaPUC
    except Exception as imp_err:
        logger.warning("contador: puc catalog import failed: %s", imp_err)
        return []

    db = None
    try:
        db = SessionLocal()
        rows = (
            db.query(CuentaPUC)
            .filter(CuentaPUC.codigo >= "4")
            .filter(CuentaPUC.codigo < "5")
            .order_by(CuentaPUC.codigo)
            .all()
        )
        return [
            {"codigo": str(r.codigo), "descripcion": str(r.descripcion or "")}
            for r in rows
        ]
    except Exception as db_err:
        logger.warning("contador: puc catalog lookup failed: %s", db_err)
        return []
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _validate_ingreso_against_catalog(
    contador_output: dict, catalog: list[dict], state: AgentState
) -> dict:
    """When ``asientos`` includes a credit to a 4xxx account that is not in the
    sown ``cuentas_puc`` catalog, fall back to the closest 4-digit parent code
    and emit a WARNING. Pure data transformation — no DB writes.

    The contador agent already runs ``contador_puc_corrector`` to fix obvious
    invalid codes; this guard catches the remaining case where the LLM returns
    a syntactically valid 6-digit code (e.g. ``413599``) that simply does not
    exist in the empresa's PUC catalog.
    """
    if not isinstance(contador_output, dict) or not catalog:
        return contador_output
    valid_codes = {row.get("codigo") for row in catalog if row.get("codigo")}
    parent_codes_4 = {c[:4] for c in valid_codes if c and len(c) >= 4}
    asientos = contador_output.get("asientos") or []
    if not isinstance(asientos, list):
        return contador_output

    changed = False
    for entry in asientos:
        if not isinstance(entry, dict):
            continue
        if (entry.get("tipo_movimiento") or "").lower() != "credito":
            continue
        codigo = str(entry.get("cuenta_puc") or "").strip()
        if not codigo or not codigo.startswith("4"):
            continue
        if codigo in valid_codes:
            continue
        parent = codigo[:4]
        if parent in parent_codes_4 or parent in valid_codes:
            logger.warning(
                "contador: ingreso PUC %s not in catalog, falling back to parent %s",
                codigo,
                parent,
            )
            entry["cuenta_puc"] = parent
            changed = True
    if changed:
        try:
            from app.agents.audit_utils import append_finding
            from app.models.audit import AuditFinding, AuditTarget, Severity

            append_finding(
                state,
                AuditFinding(
                    target=AuditTarget.CONTADOR,
                    rule_id="CONT-INGRESO-PUC-FALLBACK",
                    severity=Severity.WARNING,
                    fixable=True,
                    responsible_agent="contador",
                    technical_message=(
                        "Cuenta de ingreso (4xxx) elegida por el LLM no existe en "
                        "cuentas_puc. Se reemplazo por la cuenta padre de 4 digitos."
                    ),
                    user_message_es=(
                        "La cuenta de ingreso elegida no existe en el catalogo PUC. "
                        "Se uso la cuenta padre como aproximacion; revise el asiento."
                    ),
                ),
            )
        except Exception as finding_err:
            logger.warning(
                "contador: could not record fallback finding: %s", finding_err
            )
    return contador_output


def contador_node(state: AgentState) -> AgentState:
    """
    Contador node: classifies raw transactions into PUC-coded journal entries.

    Reads:
        state["raw_transactions"]    – list of staged transaction dicts
        state["correction_feedback"] – schema errors from previous attempt (retry)

    Writes:
        state["contador_output"]     – ContadorOutput-compatible dict
        state["current_stage"]       – "contador"
        state["current_agent"]       – "contador"
    """
    if state.get("error"):
        logger.warning("contador: skipping due to upstream error: %s", state["error"])
        return state

    raw_transactions = state.get("raw_transactions") or []
    if not raw_transactions:
        state["error"] = "contador: no raw_transactions in state"
        logger.error(state["error"])
        return state

    is_retry = bool(state.get("correction_feedback"))
    state["current_agent"] = "contador"
    state["current_stage"] = "contador"

    append_log(
        state,
        "contador",
        "node_start",
        {
            "tx_count": len(raw_transactions),
            "is_retry": is_retry,
        },
    )

    # Enrich context with RAG-retrieved PUC context when available
    rag_context: list[dict] = []
    try:
        from app.services.rag_service import get_rag_service

        rag_svc = get_rag_service()
        first_tx = raw_transactions[0] if raw_transactions else {}
        query_text = (
            first_tx.get("descripcion") or first_tx.get("concepto") or "gasto general"
        )
        rag_results = rag_svc.search_normativo(query_text, n_results=5)
        rag_context = rag_results if isinstance(rag_results, list) else []
    except Exception as rag_err:
        logger.warning("contador: RAG lookup failed (non-fatal): %s", rag_err)
        from app.agents.audit_utils import append_finding
        from app.models.audit import AuditFinding, AuditTarget, Severity

        append_finding(
            state,
            AuditFinding(
                target=AuditTarget.CONTADOR,
                rule_id="CONT-RAG-MISS",
                severity=Severity.WARNING,
                fixable=False,
                responsible_agent="contador",
                technical_message=f"RAG lookup failed: {rag_err}",
                user_message_es="No se encontró la cuenta en el catálogo PUC. La clasificación puede ser imprecisa.",
            ),
        )

    try:
        llm = get_llm_client()

        if is_retry:
            logger.info(
                "contador: retry attempt %d with correction feedback",
                state.get("retry_count", 1),
            )

        doc_type_full = (state.get("document_classification") or {}).get("doc_type", "")
        doc_type_normalized = _normalize_doc_type_for_schema(doc_type_full)
        is_venta = doc_type_full in _VENTA_DOC_TYPES
        is_nomina = doc_type_full == "nomina"
        is_cesantias = doc_type_full == "liquidacion_cesantias"

        # Extract tax summary from the rich source document (populated by ingest pipeline)
        source_doc = state.get("source_document") or {}
        source_taxes: dict | None = None
        if source_doc:
            source_taxes = _extract_source_taxes_summary(source_doc)

        # Pre-armed asiento passthrough: when the document already shows a
        # balanced journal entry (CE / RC / payroll voucher / manual journal),
        # skip the LLM entirely and persist those lines verbatim. The PUC
        # corrector runs afterwards to normalise out-of-catalogue subaccounts.
        prearmed = _extract_prearmed_asientos(source_doc)
        if prearmed:
            from decimal import Decimal as _Decimal

            total_d = sum(
                _Decimal(a["valor"])
                for a in prearmed
                if a["tipo_movimiento"] == "debito"
            )
            total_c = sum(
                _Decimal(a["valor"])
                for a in prearmed
                if a["tipo_movimiento"] == "credito"
            )
            descripcion_general = (
                source_doc.get("concepto")
                or (raw_transactions[0] or {}).get("descripcion")
                or doc_type_full
                or "comprobante"
            )
            fecha_registro = (
                source_doc.get("fecha")
                or source_doc.get("fecha_emision")
                or source_doc.get("fecha_documento")
                or (raw_transactions[0] or {}).get("fecha")
                or ""
            )
            if isinstance(fecha_registro, str) and "T" in fecha_registro:
                fecha_registro = fecha_registro.split("T")[0]
            contador_output = {
                "fecha_registro": str(fecha_registro),
                "asientos": prearmed,
                "descripcion_general": str(descripcion_general)[:255],
                "tipo_documento": doc_type_normalized or "otro",
                "total_debitos": str(total_d),
                "total_creditos": str(total_c),
            }
            contador_output = correct_contador_output(
                contador_output, doc_subtype=doc_type_full
            )
            state["correction_feedback"] = None
            state["contador_output"] = contador_output
            state["interpreted_data"] = contador_output
            if not state.get("result"):
                state["result"] = {}
            state["result"]["contador_output"] = contador_output
            state["result"]["status"] = "clasificado"
            append_log(
                state,
                "contador",
                "prearmed_passthrough",
                {
                    "lines": len(prearmed),
                    "total_debitos": str(total_d),
                    "total_creditos": str(total_c),
                },
            )
            logger.info(
                "contador: pre-armed passthrough applied (%d lines, D=C=%s)",
                len(prearmed),
                total_d,
            )
            return state

        # Enrich the prompt with empresa context + PUC ingresos catalog so the
        # LLM picks a 4xxx code that matches the actividad económica. Only
        # bother loading the catalog for venta-like docs (it's the only path
        # that credits 4xxx). For nóminas and cesantías, load company context for
        # payroll classification. For compra-like docs the prompt stays slim.
        company_context: dict | None = None
        puc_ingresos_catalog: list[dict] = []
        if is_venta:
            company_nit = state.get("company_nit")
            if not company_nit:
                for tx in raw_transactions:
                    if isinstance(tx, dict):
                        company_nit = tx.get("company_nit") or tx.get("nit_receptor")
                        if company_nit:
                            break
            company_context = _load_company_context(company_nit)
            puc_ingresos_catalog = _load_puc_ingresos_catalog()
        elif is_nomina or is_cesantias:
            # For payroll documents (nomina, liquidacion_cesantias), load company context
            # to understand cost centers, departamentos, and labor regime.
            company_nit = state.get("company_nit")
            if not company_nit:
                for tx in raw_transactions:
                    if isinstance(tx, dict):
                        company_nit = (
                            tx.get("company_nit")
                            or tx.get("empresa", {}).get("nit")
                            or tx.get("nit")
                        )
                        if company_nit:
                            break
            company_context = _load_company_context(company_nit)

        contador_output = llm_with_parse_retry(
            llm.extract_contador_output,
            raw_transactions=raw_transactions,
            doc_type=doc_type_normalized,
            doc_subtype=doc_type_full,
            rag_context=rag_context,
            correction_feedback=state.get("correction_feedback") if is_retry else None,
            source_taxes=source_taxes,
            company_context=company_context,
            puc_ingresos_catalog=puc_ingresos_catalog or None,
            agent_label="contador",
        )

        # Rewrite generic 5195 fallbacks to specific PUC subaccounts, swap CE
        # 220505 cred -> 111005, and specialize 4-digit class codes. Runs
        # before the validator so the persisted asiento uses concrete codes.
        contador_output = correct_contador_output(
            contador_output, doc_subtype=doc_type_full
        )

        # For venta docs, soft-validate that the credit ingreso account is in
        # the sown cuentas_puc catalog. If not, fall back to the parent.
        if is_venta and puc_ingresos_catalog:
            contador_output = _validate_ingreso_against_catalog(
                contador_output, puc_ingresos_catalog, state
            )

        # Clear correction feedback after consuming it
        state["correction_feedback"] = None

        state["contador_output"] = contador_output
        state["interpreted_data"] = contador_output  # keep in sync for validators

        if not state.get("result"):
            state["result"] = {}
        state["result"]["contador_output"] = contador_output
        state["result"]["status"] = "clasificado"

        logger.info("contador: classification complete")
        append_log(state, "contador", "node_complete", {"stage": "classifying"})

    except Exception as exc:
        # Surface actionable, Spanish audit findings for known parse failures
        # before terminating, so the accountant gets a useful trace instead of
        # a generic "contador error".
        if is_parse_error(exc) and is_double_entry_violation(exc):
            # Recovery attempt: ask the LLM to balance via suspense account 139595.
            # If it succeeds the pipeline continues with a WARNING instead of failing.
            logger.warning(
                "contador: double-entry violation after retries — attempting suspense recovery"
            )
            try:
                suspense_feedback = (
                    f"El asiento generado no está balanceado (débitos ≠ créditos). "
                    f"Añade UNA línea adicional a la cuenta 139595 (Cuentas por "
                    f"Aclarar) con el tipo de movimiento necesario para igualar los "
                    f"totales. No omitas los asientos ya generados. Error original: {exc}"
                )
                contador_output = llm_with_parse_retry(
                    llm.extract_contador_output,
                    raw_transactions=raw_transactions,
                    doc_type=doc_type_normalized,
                    doc_subtype=doc_type_full,
                    rag_context=rag_context,
                    correction_feedback=suspense_feedback,
                    source_taxes=source_taxes,
                    company_context=company_context,
                    puc_ingresos_catalog=puc_ingresos_catalog or None,
                    agent_label="contador-recovery",
                )
                contador_output = correct_contador_output(
                    contador_output, doc_subtype=doc_type_full
                )
                if is_venta and puc_ingresos_catalog:
                    contador_output = _validate_ingreso_against_catalog(
                        contador_output, puc_ingresos_catalog, state
                    )
                # Recovery succeeded — emit WARNING so the accountant is notified
                from app.agents.audit_utils import append_finding
                from app.models.audit import AuditFinding, AuditTarget, Severity

                append_finding(
                    state,
                    AuditFinding(
                        target=AuditTarget.CONTADOR,
                        rule_id="CONT-SUSPENSE-USED",
                        severity=Severity.WARNING,
                        fixable=True,
                        responsible_agent="contador",
                        technical_message=str(exc)[:500],
                        user_message_es=(
                            "El asiento no pudo balancearse automáticamente y se "
                            "registró una línea en la cuenta 139595 (Cuentas por "
                            "Aclarar) para mantener la partida doble."
                        ),
                        suggested_action_es=(
                            "Revise el asiento generado y reclasifique la línea de "
                            "139595 a la cuenta correcta según el documento fuente."
                        ),
                        evidence={"original_violation": str(exc)[:200]},
                    ),
                )

                state["correction_feedback"] = None
                state["contador_output"] = contador_output
                state["interpreted_data"] = contador_output

                if not state.get("result"):
                    state["result"] = {}
                state["result"]["contador_output"] = contador_output
                state["result"]["status"] = "clasificado"

                logger.info(
                    "contador: suspense recovery succeeded — pipeline continues"
                )
                append_log(
                    state,
                    "contador",
                    "node_complete",
                    {"stage": "classifying", "suspense_recovery": True},
                )
                return state

            except Exception as recovery_exc:
                logger.error(
                    "contador: suspense recovery also failed: %s", recovery_exc
                )
                # Fall through to hard failure below, using original exc

        if is_parse_error(exc):
            from app.agents.audit_utils import append_finding
            from app.models.audit import AuditFinding, AuditTarget, Severity

            if is_double_entry_violation(exc):
                rule_id = "CONT-BALANCE-UNFIXABLE"
                user_msg_es = (
                    "El documento no contiene información suficiente para generar "
                    "un asiento de doble entrada balanceado tras varios intentos, "
                    "incluyendo el intento de recuperación con cuenta puente. "
                    "Revise el documento fuente: puede estar incompleto, ser de "
                    "una sola entrada (recibo, comprobante parcial), o tener "
                    "valores inconsistentes."
                )
                action_es = (
                    "Verifique que el documento incluya tanto el cargo como el "
                    "abono. Si es un documento parcial, complete la contraparte "
                    "manualmente o use una cuenta puente (139595 - Cuentas por "
                    "Aclarar) y reclasifique después."
                )
            elif is_invalid_puc(exc):
                rule_id = "CONT-INVALID-PUC"
                user_msg_es = (
                    "El modelo generó un código PUC inválido (placeholder o "
                    "formato incorrecto) y no logró corregirlo tras varios "
                    "intentos."
                )
                action_es = (
                    "Reintente con un documento más legible o reclasifique "
                    "manualmente el asiento usando códigos PUC reales del plan "
                    "de cuentas (1-6 dígitos numéricos)."
                )
            else:
                rule_id = "CONT-PARSE-EXHAUSTED"
                user_msg_es = (
                    "El modelo no logró producir una salida válida tras varios "
                    "intentos."
                )
                action_es = (
                    "Reintente el procesamiento. Si persiste, revise el "
                    "documento fuente o reclasifique manualmente."
                )

            append_finding(
                state,
                AuditFinding(
                    target=AuditTarget.CONTADOR,
                    rule_id=rule_id,
                    severity=Severity.BLOCKER,
                    fixable=False,
                    responsible_agent="contador",
                    technical_message=str(exc)[:500],
                    user_message_es=user_msg_es,
                    suggested_action_es=action_es,
                    evidence={"exception_type": exc.__class__.__name__},
                ),
            )

        state["error"] = f"contador error: {exc}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "contador", "node_error", {"error": str(exc)})
        if not state.get("result"):
            state["result"] = {}
        state["result"]["status"] = "error"
        state["result"]["error"] = state["error"]

    return state
