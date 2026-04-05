"""
DB Persist node for the LangGraph pipeline.


Persists ingest/process outputs to PostgreSQL:
IngestJob -> TransactionPending -> TransactionPosted -> JournalEntryLines.

"""

# type: ignore[assignment]
# SQLAlchemy model attributes are runtime values on instances; static typing
# can mis-infer them as Column[...] in service/pipeline code.

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy.exc import OperationalError as SAOperationalError

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.database import IngestStatus, ProcessStatus, TransactionPending
from app.services import db_service
from app.services.db_service import financial_statements_exist, get_journal_entry_period
from app.services.financial_statement_service import (
    BusinessRuleError,
    build_first_level_from_journal_entries,
)
from app.services.financial_statement_service import (
    derive_financial_statements as _derive_financial_statements,
)
from app.services.nit_utils import normalize_optional_nit

logger = get_logger("app.agents.persist")

MAX_NODE_RETRIES = 3


def _as_str(value: Any, default: str = "") -> str:
    """Normalize possibly-ORM values to plain strings."""

    if value is None:
        return default
    return str(value)


def _sanitize_for_json(value: Any) -> Any:
    """Recursively convert non-JSON-serializable types to safe types."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    return value


def _safe_decimal(value: Any) -> Optional[Decimal]:

    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _infer_total_from_items(items: Any) -> Optional[Decimal]:
    """Best-effort total inference from extracted line items."""
    if not isinstance(items, list) or not items:
        return None

    inferred = Decimal("0")
    used_any = False

    for item in items:
        if not isinstance(item, dict):
            continue

        # Prefer explicit per-line totals when present.
        for key in ("valor_total_sin_impuesto", "valor_total", "total", "subtotal"):
            line_total = _safe_decimal(item.get(key))
            if line_total is not None:
                inferred += line_total
                used_any = True
                break
        else:
            # Fallback to unit value if line total is absent.
            unit_value = _safe_decimal(item.get("valor_unitario"))
            if unit_value is not None:
                qty = _safe_decimal(item.get("cantidad"))
                if qty is not None and qty > 0 and qty <= Decimal("10000"):
                    inferred += unit_value * qty
                else:
                    inferred += unit_value
                used_any = True

    if not used_any:
        return None
    return inferred


def _build_structured_transactions(
    interpreted: dict[str, Any], doc_type: str
) -> list[dict[str, Any]]:
    """Map rich document schemas into one or more tx rows for persistence."""

    emisor = interpreted.get("emisor") or {}
    receptor = interpreted.get("receptor") or {}
    totales = interpreted.get("totales") or {}
    items_payload = interpreted.get("items") or interpreted.get("detalle_items") or []

    # --- Doc-type specific mapping ---
    if doc_type == "extracto_bancario":
        titular = interpreted.get("titular") or {}
        movements = interpreted.get("movements") or []
        txs: list[dict[str, Any]] = []

        if isinstance(movements, list):
            for movement in movements:
                if not isinstance(movement, dict):
                    continue

                debito = _safe_decimal(movement.get("debito")) or Decimal("0")
                credito = _safe_decimal(movement.get("credito")) or Decimal("0")
                valor = debito if debito > Decimal("0") else credito
                if valor <= Decimal("0"):
                    continue

                descripcion = _as_str(
                    movement.get("descripcion"), "Movimiento bancario"
                )
                referencia = _as_str(movement.get("referencia"), "").strip()
                if referencia:
                    descripcion = f"{descripcion} (ref: {referencia})"

                txs.append(
                    {
                        "fecha": movement.get("fecha")
                        or interpreted.get("periodo_fin")
                        or interpreted.get("periodo_inicio"),
                        "nit_emisor": _as_str(
                            titular.get("nit") or interpreted.get("nit_emisor"), ""
                        ),
                        "nit_receptor": _as_str(
                            interpreted.get("nit_receptor") or receptor.get("nit"), ""
                        ),
                        "total": str(valor),
                        "concepto": descripcion,
                        "descripcion": descripcion,
                        "items": [_sanitize_for_json(movement)],
                    }
                )

        if txs:
            return txs

        resumen = interpreted.get("resumen") or {}
        fallback_total = (
            _safe_decimal((resumen or {}).get("total_debitos"))
            or _safe_decimal((resumen or {}).get("total_creditos"))
            or _safe_decimal(interpreted.get("saldo_final"))
            or Decimal("0")
        )
        return [
            {
                "fecha": interpreted.get("periodo_fin")
                or interpreted.get("periodo_inicio"),
                "nit_emisor": _as_str(titular.get("nit"), ""),
                "nit_receptor": _as_str(
                    interpreted.get("nit_receptor") or receptor.get("nit"), ""
                ),
                "total": str(fallback_total),
                "concepto": "Extracto bancario",
                "descripcion": "Extracto bancario",
                "items": _sanitize_for_json(
                    movements if isinstance(movements, list) else []
                ),
            }
        ]

    if doc_type == "nomina":
        empresa = interpreted.get("empresa") or {}
        periodo_inicio = _as_str(interpreted.get("periodo_inicio"), "")
        periodo_fin = _as_str(interpreted.get("periodo_fin"), "")
        periodo_txt = ""
        if periodo_inicio and periodo_fin:
            periodo_txt = f"Periodo {periodo_inicio} a {periodo_fin}"
        elif periodo_inicio:
            periodo_txt = f"Periodo desde {periodo_inicio}"
        elif periodo_fin:
            periodo_txt = f"Periodo hasta {periodo_fin}"

        raw_total = (
            interpreted.get("total_neto_pagar")
            or interpreted.get("total_devengado")
            or interpreted.get("total")
        )
        parsed_total = _safe_decimal(raw_total)
        if parsed_total is None:
            empleados = interpreted.get("empleados") or []
            if isinstance(empleados, list):
                parsed_total = sum(
                    [
                        _safe_decimal((e or {}).get("neto_pagar")) or Decimal("0")
                        for e in empleados
                        if isinstance(e, dict)
                    ],
                    Decimal("0"),
                )
            else:
                parsed_total = Decimal("0")

        concepto = "Nomina"
        if periodo_txt:
            concepto = f"Nomina - {periodo_txt}"

        return [
            {
                "fecha": interpreted.get("periodo_fin")
                or interpreted.get("periodo_inicio")
                or interpreted.get("fecha"),
                "nit_emisor": _as_str(
                    empresa.get("nit") or interpreted.get("nit_emisor"), ""
                ),
                "nit_receptor": _as_str(
                    interpreted.get("nit_receptor") or receptor.get("nit"), ""
                ),
                "total": str(parsed_total),
                "concepto": concepto,
                "descripcion": concepto,
                "items": _sanitize_for_json(interpreted.get("empleados") or []),
            }
        ]

    if doc_type == "recibo_pago_impuesto":
        raw_total = (
            interpreted.get("total_pagado")
            or interpreted.get("valor_principal")
            or interpreted.get("total")
        )
        parsed_total = _safe_decimal(raw_total) or Decimal("0")
        tipo_impuesto = _as_str(interpreted.get("tipo_impuesto"), "")
        periodo_gravable = _as_str(interpreted.get("periodo_gravable"), "")
        concepto = "Pago de impuesto"
        if tipo_impuesto:
            concepto = f"Pago de impuesto {tipo_impuesto}"
        if periodo_gravable:
            concepto = f"{concepto} ({periodo_gravable})"

        return [
            {
                "fecha": interpreted.get("fecha_pago") or interpreted.get("fecha"),
                "nit_emisor": _as_str(
                    interpreted.get("nit_declarante") or interpreted.get("nit_emisor"),
                    "",
                ),
                "nit_receptor": _as_str(
                    interpreted.get("nit_receptor") or receptor.get("nit"), ""
                ),
                "total": str(parsed_total),
                "concepto": concepto,
                "descripcion": concepto,
                "items": _sanitize_for_json(
                    [
                        {
                            "numero_recibo": interpreted.get("numero_recibo"),
                            "entidad_fiscal": interpreted.get("entidad_fiscal"),
                            "banco": interpreted.get("banco"),
                            "referencia_pago": interpreted.get("referencia_pago"),
                            "valor_principal": interpreted.get("valor_principal"),
                            "sanciones": interpreted.get("sanciones"),
                            "intereses": interpreted.get("intereses"),
                            "total_pagado": interpreted.get("total_pagado"),
                        }
                    ]
                ),
            }
        ]

    # --- Generic fallback mapping ---
    raw_total = (
        # Invoice-like schemas
        totales.get("total_a_pagar")
        or totales.get("total")
        # Voucher-like schemas
        or interpreted.get("valor_neto")
        or interpreted.get("valor_bruto")
        # Generic fallbacks
        or interpreted.get("total")
        or interpreted.get("valor_total")
        or interpreted.get("valor")
        or interpreted.get("monto")
    )
    parsed_total = _safe_decimal(raw_total)
    if parsed_total is None or parsed_total == Decimal("0"):
        inferred_total = _infer_total_from_items(items_payload)
        if inferred_total is not None and inferred_total > Decimal("0"):
            logger.info(
                "db_persist: inferred total=%s from line items for structured ingest",
                inferred_total,
            )
            parsed_total = inferred_total

    tx_data = {
        "fecha": (
            interpreted.get("fecha_emision")
            or interpreted.get("fecha_registro")
            or interpreted.get("fecha")
        ),
        "nit_emisor": _as_str(emisor.get("nit") or interpreted.get("nit_emisor"), ""),
        "nit_receptor": _as_str(
            receptor.get("nit") or interpreted.get("nit_receptor"), ""
        ),
        "total": str(parsed_total if parsed_total is not None else Decimal("0")),
        "concepto": _as_str(
            interpreted.get("descripcion_general")
            or interpreted.get("concepto")
            or interpreted.get("tipo_documento", ""),
            "",
        ),
        "descripcion": _as_str(
            interpreted.get("descripcion_general")
            or interpreted.get("concepto")
            or interpreted.get("tipo_documento", ""),
            "",
        ),
        "items": _sanitize_for_json(items_payload),
    }
    return [tx_data]


def _resolve_company_nit(
    state: AgentState, tx_data: dict[str, Any] | None = None
) -> Optional[str]:
    """Resolve tenant company NIT with explicit override precedence."""
    if state.get("company_nit"):
        return normalize_optional_nit(state.get("company_nit"))

    classification = state.get("document_classification") or {}
    class_nit = classification.get("entity_nit")
    if class_nit:
        return normalize_optional_nit(class_nit)

    if tx_data:
        receiver_nit = tx_data.get("nit_receptor")
        if receiver_nit:
            return normalize_optional_nit(receiver_nit)

    return None


def db_persist_node(state: AgentState) -> AgentState:
    """Persist current state output to DB for ingest/process mode."""

    if state.get("error"):
        logger.warning("db_persist: Skipping due to upstream error: %s", state["error"])
        return state

    append_log(state, "db_persist", "node_start", {"mode": state.get("mode", "ingest")})

    for _attempt in range(1, MAX_NODE_RETRIES + 1):
        try:
            _db_persist_inner(state)
            if state.get("error"):
                append_log(state, "db_persist", "node_error", {"error": state["error"]})
                return state
            append_log(
                state,
                "db_persist",
                "node_complete",
                {
                    "ingest_id": state.get("ingest_id"),
                },
            )
            return state
        except SAOperationalError as e:
            logger.warning(
                f"db_persist: transient DB error attempt {_attempt}/{MAX_NODE_RETRIES}: {e}"
            )
            if _attempt == MAX_NODE_RETRIES:
                state["error"] = (
                    f"DB persist failed after {MAX_NODE_RETRIES} attempts: {e}"
                )
                append_log(state, "db_persist", "node_error", {"error": str(e)})
                return state
        except Exception:
            # Non-transient — fall through to original error handling below
            break

    # Non-retry path: wrap in try/except to handle non-transient exceptions
    try:
        _db_persist_inner_with_cleanup(state)
        if state.get("error"):
            append_log(state, "db_persist", "node_error", {"error": state["error"]})
        else:
            append_log(
                state,
                "db_persist",
                "node_complete",
                {
                    "ingest_id": state.get("ingest_id"),
                },
            )
    except Exception as e:
        logger.error(
            f"db_persist: Non-transient exception in cleanup path: {e}", exc_info=True
        )
        state["error"] = f"DB persist error: {str(e)}"
        append_log(state, "db_persist", "node_error", {"error": str(e)})

    return state


def _db_persist_inner(state: AgentState) -> None:
    """Run the core DB persistence; raises on any error (called inside retry loop)."""
    _run_persist(state)


def _db_persist_inner_with_cleanup(state: AgentState) -> AgentState:
    """Run persistence with full error cleanup; used when retry loop is exhausted/skipped."""
    _run_persist(state)
    return state


def _auto_derive_statements(db, company_nit: str) -> None:
    """Build first-level statements from JournalEntryLines then derive second-level.

    Non-fatal: logs warnings on failure but never raises.
    """
    if not company_nit:
        return

    period = get_journal_entry_period(db, company_nit=company_nit)
    if period is None:
        logger.warning(
            "[persist] No JournalEntryLines for %s — skipping statement derivation",
            company_nit,
        )
        return

    period_start, period_end = period

    # Guard: ensure period values are real datetimes (not Mock objects from tests)
    if not isinstance(period_start, datetime) or not isinstance(period_end, datetime):
        logger.warning(
            "[persist] Unexpected period type (%s, %s) — skipping derivation",
            type(period_start).__name__, type(period_end).__name__,
        )
        return

    logger.info(
        "[persist] Building first-level statements for %s (%s -> %s)",
        company_nit,
        period_start.date(),
        period_end.date(),
    )

    try:
        build_first_level_from_journal_entries(
            db,
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
        )
    except Exception as exc:
        # Non-fatal: missing derived statements don't break the accounting pipeline.
        # The request still succeeds; derivation can be re-triggered on next run.
        logger.warning("[persist] build_first_level failed (non-fatal): %s", exc, exc_info=True)
        return

    try:
        _derive_financial_statements(
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
        )
    except BusinessRuleError as exc:
        logger.warning("[persist] derive skipped (missing source inputs): %s", exc)
    except Exception as exc:
        logger.warning("[persist] derive failed (non-fatal): %s", exc, exc_info=True)


def _try_via_b_auto_derive(db, *, company_nit: str, period_start, period_end) -> None:
    """After a Via B upload, check if all 3 source docs are present and derive if so.

    Non-fatal: logs but never raises.
    """
    if not company_nit or period_start is None or period_end is None:
        return

    required = ["balance_general", "estado_resultados", "libro_auxiliar"]
    if not financial_statements_exist(
        db,
        company_nit=company_nit,
        period_start=period_start,
        period_end=period_end,
        types=required,
    ):
        logger.info(
            "[persist] Via B: not all 3 source docs present yet for %s — skipping auto-derive",
            company_nit,
        )
        return

    logger.info(
        "[persist] Via B: all 3 source docs present for %s — triggering derivation",
        company_nit,
    )
    # Fail fast on derivation errors except expected precondition mismatches.
    try:
        _derive_financial_statements(
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
        )
    except BusinessRuleError as exc:
        logger.warning("[persist] Via B derive skipped: %s", exc)


def _run_persist(state: AgentState) -> AgentState:
    """Core persistence logic. Raises on failure; called by the retry wrappers."""
    mode = state.get("mode", "ingest")
    pathway = state.get("pathway", "build_from_scratch")
    interpreted = state.get("interpreted_data", {}) or {}
    classification = state.get("document_classification") or {}
    doc_type = _as_str(classification.get("doc_type"), "")

    # --- Vía B: persist existing financial statement directly ---
    if mode == "ingest" and pathway == "work_with_existing":
        _persist_financial_statement(state)
        return state

    if mode == "process":
        contador_output = state.get("contador_output") or interpreted
        asientos = (
            contador_output.get("asientos", [])
            if isinstance(contador_output, dict)
            else []
        )
        if not asientos:
            msg = "db_persist: No contador asientos to persist"
            logger.error(msg)
            state["error"] = msg
            raise RuntimeError(msg)

        raw_txs = state.get("raw_transactions") or []
        base_tx = raw_txs[0] if raw_txs and isinstance(raw_txs[0], dict) else {}

        total = base_tx.get("total")
        if total is None:
            total = (
                contador_output.get("total_debitos")
                or contador_output.get("total_creditos")
                or 0
            )

        fecha = base_tx.get("fecha") or contador_output.get("fecha_registro")
        nit_emisor = base_tx.get("nit_emisor", "")
        nit_receptor = base_tx.get("nit_receptor", "")
        descripcion = base_tx.get("descripcion") or contador_output.get(
            "descripcion_general", ""
        )
        items = base_tx.get("items", [])

        debit_line = next(
            (
                a
                for a in asientos
                if str(a.get("tipo_movimiento", "")).lower() == "debito"
            ),
            None,
        )

        # Pull tax values from tributario_output so they are persisted correctly.
        tributario_output = state.get("tributario_output") or {}
        trib_impuestos = tributario_output.get("impuestos", [])

        def _get_trib_tax(tipo: str) -> Optional[str]:
            val = next(
                (
                    i.get("valor_impuesto")
                    for i in trib_impuestos
                    if i.get("tipo_impuesto") == tipo
                ),
                None,
            )
            return str(val) if val is not None else None

        tx_data = {
            "fecha": fecha,
            "nit_emisor": nit_emisor,
            "nit_receptor": nit_receptor,
            "total": total,
            "concepto": descripcion,
            "descripcion": descripcion,
            "items": items,
            "cuenta_puc": (debit_line or {}).get("cuenta_puc", ""),
            "cuenta_nombre": (debit_line or {}).get("nombre_cuenta", ""),
            "retefuente": _get_trib_tax("retefuente"),
            "reteica": _get_trib_tax("reteica"),
            "iva": _get_trib_tax("IVA"),
            "ica": _get_trib_tax("ica"),
            "renta": _get_trib_tax("renta"),
            "referencias_legales": tributario_output.get("referencias_legales", []),
            "agent_reasoning": (state.get("result") or {}).get("agent_reasoning"),
            "_contador_asientos": asientos,
        }
        transactions = [tx_data]
    else:
        # New rich-schema path: interpreted_data is a typed content dict (FacturaVentaContent, etc.)
        # Build one or multiple tx rows from structured fields.
        if isinstance(interpreted, dict) and "transactions" not in interpreted:
            transactions = _build_structured_transactions(interpreted, doc_type)
        else:
            transactions = (
                interpreted.get("transactions", [])
                if isinstance(interpreted, dict)
                else []
            )
        if not transactions:
            msg = "db_persist: No transactions to persist"
            logger.warning(msg)
            state["error"] = msg
            raise RuntimeError(msg)

        # Expose built transactions so callers can access them via result["raw_transactions"]
        if not state.get("raw_transactions"):
            state["raw_transactions"] = transactions

    ingest_id = _as_str(state.get("ingest_id"), "")
    db = SessionLocal()

    try:
        # ── 1. Create or update IngestJob ──

        if ingest_id:
            ingest_job = db_service.get_ingest_job(db, ingest_id)
            if ingest_job:
                db_service.update_ingest_job(
                    db,
                    ingest_id,
                    IngestStatus.PROCESSING,
                    raw_preview=_build_preview(transactions[0], doc_type),
                )
        else:
            file_name = state.get("file_path", "unknown.pdf").split("/")[-1]
            ingest_job = db_service.create_ingest_job(
                db, file_name, state.get("file_path")
            )
            ingest_id = _as_str(getattr(ingest_job, "id", ""), "")
            state["ingest_id"] = ingest_id

        total_lines = 0
        total_duplicates = 0
        posted_ids: list[str] = []
        pending_ids: list[str] = []

        if mode == "process":
            process_id = _as_str(state.get("process_id"), "")
            if process_id:
                db_service.update_process_job(
                    db,
                    process_id,
                    status=ProcessStatus.RUNNING,
                    current_stage="persisting",
                    current_agent="db_persist",
                    progress=85,
                    agent_log_entry={
                        "agent": "db_persist",
                        "stage": "persisting",
                        "status": "running",
                    },
                )

        for tx_data in transactions:
            fecha = _safe_datetime(tx_data.get("fecha")) or datetime.now(timezone.utc)
            total = _safe_decimal(
                tx_data.get("total") or tx_data.get("valor_total")
            ) or Decimal("0")
            nit_emisor = _as_str(tx_data.get("nit_emisor"), "").strip()
            nit_receptor = _as_str(tx_data.get("nit_receptor"), "").strip()
            company_nit = _resolve_company_nit(state, tx_data)
            if not nit_receptor and company_nit:
                nit_receptor = company_nit
                logger.warning(
                    "db_persist: nit_receptor missing in extracted transaction; using company_nit=%s",
                    company_nit,
                )
            descripcion = _as_str(
                tx_data.get("concepto") or tx_data.get("descripcion"), ""
            )
            items = tx_data.get("items") or tx_data.get("detalle_items") or []

            if mode == "process" and state.get("pending_transaction_id"):
                pending_id = _as_str(state.get("pending_transaction_id"), "")
                txn_pending = (
                    db.query(TransactionPending)
                    .filter(TransactionPending.id == pending_id)
                    .first()
                )
                if not txn_pending:
                    msg = "DB persist error: pending transaction not found for process mode"
                    logger.error(msg)
                    state["error"] = msg
                    raise RuntimeError(msg)
            else:
                txn_pending = db_service.create_transaction_pending(
                    db,
                    ingest_id=ingest_id,
                    company_nit=company_nit,
                    fecha=fecha,
                    nit_emisor=nit_emisor,
                    nit_receptor=nit_receptor,
                    total=total,
                    descripcion=descripcion,
                    items=items if isinstance(items, list) else [],
                    raw_data=tx_data,
                )
                logger.info(f"db_persist: Created TransactionPending {txn_pending.id}")

            pending_ids.append(_as_str(getattr(txn_pending, "id", ""), ""))

            duplicates = []
            if nit_emisor and total and fecha:
                duplicates = db_service.check_duplicates(db, nit_emisor, total, fecha)
                txn_pending_id = _as_str(getattr(txn_pending, "id", ""), "")
                duplicates = [
                    d
                    for d in duplicates
                    if _as_str(getattr(d, "id", ""), "") != txn_pending_id
                ]
                if duplicates:
                    total_duplicates += len(duplicates)
                    logger.warning(
                        f"db_persist: Found {len(duplicates)} potential duplicates for "
                        f"NIT {nit_emisor}, total={total}"
                    )

            if mode == "process":
                asientos = tx_data.get("_contador_asientos", [])
                debit_line = next(
                    (
                        a
                        for a in asientos
                        if str(a.get("tipo_movimiento", "")).lower() == "debito"
                    ),
                    None,
                )
                cuenta_puc = _as_str((debit_line or {}).get("cuenta_puc"), "")
                puc_descripcion = _as_str((debit_line or {}).get("nombre_cuenta"), "")
                if not cuenta_puc:
                    msg = "DB persist error: contador output missing debit cuenta_puc"
                    logger.error(msg)
                    state["error"] = msg
                    raise RuntimeError(msg)
            else:
                cuenta_puc = _as_str(tx_data.get("cuenta_puc"), "")
                if not cuenta_puc:
                    logger.warning(
                        "db_persist: No PUC code in ingest data — "
                        "defaulting to 519595 (Otros Gastos). "
                        "Run accounting pipeline to classify properly."
                    )
                    cuenta_puc = "519595"
                puc_descripcion = _as_str(tx_data.get("cuenta_nombre"), "")

            puc_record = db_service.validate_puc_exists(db, cuenta_puc)
            if puc_record:
                puc_descripcion = _as_str(getattr(puc_record, "nombre", ""), "")
            elif mode == "process":
                msg = f"DB persist error: PUC code {cuenta_puc} not found"
                logger.error(msg)
                state["error"] = msg
                raise RuntimeError(msg)
            else:
                logger.warning(f"db_persist: PUC code {cuenta_puc} not found")

            retefuente = _safe_decimal(tx_data.get("retefuente")) or Decimal("0")
            reteica = _safe_decimal(tx_data.get("reteica")) or Decimal("0")
            iva = _safe_decimal(
                tx_data.get("iva") or tx_data.get("iva_valor")
            ) or Decimal("0")
            ica = _safe_decimal(tx_data.get("ica")) or Decimal("0")
            provision_renta = _safe_decimal(tx_data.get("renta")) or Decimal("0")
            neto = _safe_decimal(tx_data.get("neto_a_pagar")) or total

            if mode == "process":
                journal_json = _journal_entries_from_contador(
                    fecha=fecha,
                    asientos=tx_data.get("_contador_asientos", []),
                    nit=nit_emisor,
                    descripcion=descripcion,
                )
                neto = total
                tax_references = tx_data.get("referencias_legales", [])
                auditor_out = state.get("auditor_output") or {}
                agent_reasoning = {
                    "contador": contador_output,
                    "auditor": auditor_out,
                }
            else:
                journal_json = _build_journal_entries(
                    fecha=fecha,
                    cuenta_puc=cuenta_puc,
                    puc_descripcion=puc_descripcion,
                    total=total,
                    iva=iva,
                    retefuente=retefuente,
                    reteica=reteica,
                    nit=nit_emisor,
                    descripcion=descripcion,
                )
                tax_references = interpreted.get("referencias_legales", [])
                raw_reasoning = tx_data.get("agent_reasoning")
                agent_reasoning = (
                    raw_reasoning if isinstance(raw_reasoning, dict) else {}
                )

            txn_posted = db_service.create_transaction_posted(
                db,
                transaction_pending_id=_as_str(getattr(txn_pending, "id", "")),
                company_nit=company_nit,
                cuenta_puc=cuenta_puc,
                puc_descripcion=puc_descripcion,
                retefuente=retefuente,
                reteica=reteica,
                iva=iva,
                ica=ica,
                provision_renta=provision_renta,
                neto_a_pagar=neto,
                journal_entries_json=journal_json,
                tax_references=tax_references,
                agent_reasoning=agent_reasoning,
            )
            posted_ids.append(_as_str(getattr(txn_posted, "id", ""), ""))
            logger.info("db_persist: Created TransactionPosted %s", txn_posted.id)

            lines = db_service.create_journal_entry_lines(
                db,
                _as_str(getattr(txn_posted, "id", "")),
                journal_json,
                company_nit=company_nit,
            )
            total_lines += len(lines)
            logger.info("db_persist: Created %d journal entry lines", len(lines))

        auditor_out = state.get("auditor_output") or {}
        classification = state.get("document_classification") or {}
        doc_type = classification.get("doc_type")
        pathway_value = state.get("pathway")

        if mode == "ingest":
            db_service.update_ingest_job(
                db,
                ingest_id,
                IngestStatus.COMPLETED,
                document_type=doc_type,
                pathway=pathway_value,
            )
        else:
            process_id = _as_str(state.get("process_id"), "")
            if process_id:
                db_service.update_process_job(
                    db,
                    process_id,
                    status=ProcessStatus.COMPLETED,
                    current_stage="completed",
                    current_agent="db_persist",
                    progress=100,
                    agent_log_entry={
                        "agent": "db_persist",
                        "stage": "completed",
                        "status": "completed",
                    },
                )

        # Auto-derive financial statements after process completes (non-fatal)
        if mode == "process" and company_nit:
            _auto_derive_statements(db, company_nit)

        state["db_result"] = {
            "ingest_id": ingest_id,
            "processed_transactions": len(transactions),
            "journal_lines_count": total_lines,
            "duplicates_found": total_duplicates,
            "transaction_pending_id": pending_ids[0] if pending_ids else "",
            "transaction_posted_id": posted_ids[0] if posted_ids else "",
            "audit_approved": state.get("audit_approved"),
            "audit_nivel_riesgo": (
                auditor_out.get("nivel_riesgo") if mode == "process" else None
            ),
            "audit_puntaje_calidad": (
                auditor_out.get("puntaje_calidad") if mode == "process" else None
            ),
            "audit_hallazgos_count": (
                len(auditor_out.get("hallazgos", [])) if mode == "process" else 0
            ),
        }

        if state.get("result") is not None:
            state["result"]["db_persisted"] = True
            state["result"]["ingest_id"] = ingest_id
            state["result"]["transaction_ids"] = posted_ids
            if posted_ids:
                state["result"]["transaction_id"] = posted_ids[0]
            if mode == "process":
                state["result"]["audit_approved"] = state.get("audit_approved")
                state["result"]["audit_nivel_riesgo"] = auditor_out.get("nivel_riesgo")

        logger.info(
            "db_persist: Successfully persisted all data for ingest %s", ingest_id
        )

    except SAOperationalError:
        # Re-raise so the retry loop in db_persist_node can catch and retry
        raise
    except Exception as e:
        logger.error(f"db_persist: Error persisting data: {e}", exc_info=True)
        state["error"] = f"DB persist error: {str(e)}"
        append_log(state, "db_persist", "node_error", {"error": str(e)})

        if mode == "ingest" and ingest_id:
            try:
                db_service.update_ingest_job(
                    db,
                    ingest_id,
                    IngestStatus.FAILED,
                    extraction_errors=[str(e)],
                )
            except Exception:
                pass

        if mode == "process":
            process_id = _as_str(state.get("process_id"), "")
            if process_id:
                try:
                    db_service.update_process_job(
                        db,
                        process_id,
                        status=ProcessStatus.FAILED,
                        current_stage="failed",
                        current_agent="db_persist",
                        error_message=str(e),
                        progress=100,
                        agent_log_entry={
                            "agent": "db_persist",
                            "stage": "failed",
                            "status": "failed",
                        },
                    )
                except Exception:
                    pass
    finally:
        db.close()

    return state


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _journal_entries_from_contador(
    *, fecha: datetime, asientos: list, nit: str, descripcion: str
) -> list:

    fecha_iso = fecha.isoformat() if isinstance(fecha, datetime) else str(fecha)
    entries = []
    for asiento in asientos:
        tipo = str(asiento.get("tipo_movimiento", "")).lower()
        valor = _safe_decimal(asiento.get("valor")) or Decimal("0")
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": str(asiento.get("cuenta_puc", "")),
                "descripcion": asiento.get("nombre_cuenta") or descripcion,
                "tercero_nit": nit,
                "detalle": asiento.get("descripcion") or descripcion,
                "debito": str(valor if tipo == "debito" else Decimal("0")),
                "credito": str(valor if tipo == "credito" else Decimal("0")),
            }
        )
    return entries


def _persist_financial_statement(state: AgentState) -> None:
    """Persist a Vía B financial statement (balance, PnL, or libro auxiliar)."""
    from app.models.database import IngestStatus

    interpreted = state.get("interpreted_data") or state.get("result", {}).get(
        "data", {}
    )
    classification = state.get("document_classification") or {}
    doc_type = classification.get("doc_type", "unknown")

    if not interpreted:
        msg = "db_persist: No financial statement data to persist (Vía B)"
        logger.error(msg)
        state["error"] = msg
        raise RuntimeError(msg)

    ingest_id = _as_str(state.get("ingest_id"), "")
    db = SessionLocal()

    try:
        # Create or update IngestJob (without committing yet)
        if ingest_id:
            ingest_job = db_service.get_ingest_job(db, ingest_id)
        else:
            file_name = state.get("file_path", "unknown").split("/")[-1]
            ingest_job = db_service.create_ingest_job(
                db, file_name, state.get("file_path"), commit=False
            )
            ingest_id = _as_str(getattr(ingest_job, "id", ""), "")
            state["ingest_id"] = ingest_id

        # Populate routing metadata on the job
        if ingest_job:
            ingest_job.document_type = doc_type
            ingest_job.pathway = state.get("pathway", "work_with_existing")

        company_nit = _resolve_company_nit(state)
        if company_nit is None:
            raise ValueError(
                "Vía B persistence requires a company NIT (provided or detected)"
            )

        period_start = _safe_datetime(
            classification.get("period_start") or interpreted.get("periodo_inicio")
        )
        period_end = _safe_datetime(
            classification.get("period_end")
            or interpreted.get("periodo_fin")
            or interpreted.get("periodo_corte")
        )

        if not period_end:
            # Keep persistence backward compatible: if no period is extractable,
            # anchor the statement to current timestamp as period end.
            period_end = datetime.now(timezone.utc)

        # Create FinancialStatement record
        stmt = db_service.create_financial_statement(
            db,
            ingest_id=ingest_id,
            statement_type=doc_type,
            period_start=period_start,
            period_end=period_end,
            entity_nit=company_nit,
            source_mode="direct",
            data=interpreted,
            commit=False,
        )

        # Mark ingest as completed and commit everything in one transaction
        db_service.update_ingest_job(
            db, ingest_id, IngestStatus.COMPLETED, commit=False
        )
        db.commit()

        # Via B auto-derivation: if all 3 source statements present, derive second-level
        if (
            doc_type in ("balance_general", "estado_resultados", "libro_auxiliar")
            and company_nit
        ):
            _try_via_b_auto_derive(
                db,
                company_nit=company_nit,
                period_start=period_start,
                period_end=period_end,
            )

        state["db_result"] = {
            "ingest_id": ingest_id,
            "financial_statement_id": stmt.id,
            "statement_type": doc_type,
            "pathway": "work_with_existing",
        }

        if state.get("result") is not None:
            state["result"]["db_persisted"] = True
            state["result"]["ingest_id"] = ingest_id
            state["result"]["financial_statement_id"] = stmt.id

        logger.info(
            "db_persist: Vía B financial statement persisted (ingest=%s, stmt=%s)",
            ingest_id,
            stmt.id,
        )

    except Exception as e:
        db.rollback()
        logger.error("db_persist: Vía B persistence failed: %s", e, exc_info=True)
        state["error"] = f"DB persist error (Vía B): {str(e)}"
        if ingest_id:
            try:
                db_service.update_ingest_job(
                    db,
                    ingest_id,
                    IngestStatus.FAILED,
                    extraction_errors=[str(e)],
                )
            except Exception:
                pass
    finally:
        db.close()


def _build_preview(interpreted: dict, doc_type: str = "") -> dict:
    concepto = _as_str(interpreted.get("concepto"), "").strip()
    if not concepto:
        default_by_type = {
            "extracto_bancario": "Extracto bancario",
            "nomina": "Nomina",
            "recibo_pago_impuesto": "Pago de impuesto",
        }
        concepto = default_by_type.get(doc_type, "")

    items = interpreted.get("items")
    items_count = len(items) if isinstance(items, list) else 0

    return {
        "nit_emisor": interpreted.get("nit_emisor"),
        "total": str(interpreted.get("total", "")),
        "fecha": str(interpreted.get("fecha", "")),
        "concepto": concepto[:100],
        "items_count": items_count,
    }


def _build_journal_entries(
    fecha: datetime,
    cuenta_puc: str,
    puc_descripcion: str,
    total: Decimal,
    iva: Decimal,
    retefuente: Decimal,
    reteica: Decimal,
    nit: str,
    descripcion: str,
) -> list:
    """
    Build double-entry (partida doble) journal entries for the ingest path.

    For a typical purchase/expense:
    - DEBIT the expense account (PUC) for base (total - IVA)
    - DEBIT IVA descontable (240802) if IVA > 0
    - CREDIT vendor payable (220505) for base + IVA - retenciones
    - CREDIT retefuente (240815) if retention > 0
    - CREDIT reteICA (236540) if reteica > 0
    """

    entries = []
    base = total - iva
    fecha_iso = fecha.isoformat() if isinstance(fecha, datetime) else str(fecha)

    if base > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": cuenta_puc,
                "descripcion": puc_descripcion or descripcion,
                "tercero_nit": nit,
                "detalle": descripcion,
                "debito": str(base),
                "credito": "0",
            }
        )

    if iva > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": "240802",
                "descripcion": "IVA Descontable",
                "tercero_nit": nit,
                "detalle": f"IVA por {descripcion}",
                "debito": str(iva),
                "credito": "0",
            }
        )

    total_credito_proveedor = total - retefuente - reteica
    if total_credito_proveedor > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": "220505",
                "descripcion": "Proveedores Nacionales",
                "tercero_nit": nit,
                "detalle": f"CxP {descripcion}",
                "debito": "0",
                "credito": str(total_credito_proveedor),
            }
        )

    if retefuente > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": "240815",
                "descripcion": "Retencion en la Fuente - Servicios",
                "tercero_nit": nit,
                "detalle": f"Retefuente {descripcion}",
                "debito": "0",
                "credito": str(retefuente),
            }
        )

    if reteica > 0:
        entries.append(
            {
                "fecha": fecha_iso,
                "cuenta": "236540",
                "descripcion": "ReteICA por pagar",
                "tercero_nit": nit,
                "detalle": f"ReteICA {descripcion}",
                "debito": "0",
                "credito": str(reteica),
            }
        )

    # Validate double-entry principle (partida doble)
    total_debitos = sum(Decimal(e["debito"]) for e in entries)
    total_creditos = sum(Decimal(e["credito"]) for e in entries)
    if total_debitos != total_creditos:
        logger.error(
            "Double-entry violation in _build_journal_entries: "
            "debits (%s) != credits (%s)",
            total_debitos,
            total_creditos,
        )
        raise RuntimeError(
            f"Unbalanced journal entries: D={total_debitos} C={total_creditos}"
        )

    return entries
