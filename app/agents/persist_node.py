"""
DB Persist node for the LangGraph pipeline.


Persists ingest/process outputs to PostgreSQL:
IngestJob -> TransactionPending -> TransactionPosted -> JournalEntryLines.

"""

# type: ignore[assignment]
# SQLAlchemy model attributes are runtime values on instances; static typing
# can mis-infer them as Column[...] in service/pipeline code.

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.exc import OperationalError as SAOperationalError

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.database import DB_WRITE_SEMAPHORE, SessionLocal
from app.core.logger import get_logger
from app.models.database import IngestStatus, ProcessStatus, TransactionPending
from app.services import db_service
from app.services.db_service import financial_statements_exist, get_journal_entry_period
from app.account_process.journal_builder import JournalBuilder
from app.account_process.persist_orchestrator import PersistOrchestrator
from app.services.financial_statement_service import (
    BusinessRuleError,
)
from app.services.financial_statement_service import (
    derive_financial_statements as _derive_financial_statements,
)
from app.services.nit_utils import normalize_optional_nit
from app.services.document_mappers import (
    as_str,
    build_structured_transactions,
    safe_decimal,
    safe_datetime,
)
from app.core.retry import with_db_retry

logger = get_logger("app.agents.persist")

MAX_NODE_RETRIES = 3


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


def _resolve_cuenta_reteica(state: AgentState) -> str:
    """Extract custom ReteICA account from tributario_output, fallback to 2368."""
    tributario = state.get("tributario_output") or {}
    for imp in tributario.get("impuestos") or []:
        if imp.get("tipo_impuesto") == "reteica" and imp.get("cuenta_puc"):
            return str(imp["cuenta_puc"])
    return "2368"


def db_persist_node(state: AgentState) -> AgentState:
    """Persist current state output to DB for ingest/process mode.

    Acquires DB_WRITE_SEMAPHORE before any writes so that concurrent document
    uploads serialize at this point instead of racing on shared DB rows or
    exhausting the connection pool. LLM extraction upstream runs in parallel;
    only the write phase is serialized.
    """
    if state.get("error"):
        logger.warning("db_persist: Skipping due to upstream error: %s", state["error"])
        return state

    append_log(state, "db_persist", "node_start", {"mode": state.get("mode", "ingest")})

    logger.debug(
        "db_persist: waiting for DB write semaphore (ingest_id=%s)",
        state.get("ingest_id"),
    )
    acquired = DB_WRITE_SEMAPHORE.acquire(timeout=120)
    if not acquired:
        state["error"] = (
            "db_persist: timed out waiting for DB write semaphore (another job may be stuck)"
        )
        append_log(state, "db_persist", "semaphore_timeout", {"error": state["error"]})
        return state
    logger.debug(
        "db_persist: acquired DB write semaphore (ingest_id=%s)", state.get("ingest_id")
    )
    try:
        return _db_persist_node_inner(state)
    finally:
        DB_WRITE_SEMAPHORE.release()


def _db_persist_node_inner(state: AgentState) -> AgentState:
    """Execute DB writes — called only while holding DB_WRITE_SEMAPHORE."""

    def _persist() -> None:
        _db_persist_inner(state)

    def _on_non_transient(e: Exception) -> None:
        err_msg = state.get("error") or f"DB persist error: {str(e)}"
        state["error"] = err_msg
        if state.get("mode") == "process":
            process_id = as_str(state.get("process_id"), "")
            if process_id:
                db = SessionLocal()
                try:
                    db_service.update_process_job(
                        db,
                        process_id,
                        status=ProcessStatus.FAILED,
                        current_stage="failed",
                        current_agent="db_persist",
                        error_message=str(err_msg),
                        progress=100,
                        agent_log_entry={
                            "agent": "db_persist",
                            "stage": "failed",
                            "status": "failed",
                        },
                    )
                except Exception as status_err:
                    logger.debug(
                        "persist_node: failed to mark process job FAILED: %s",
                        status_err,
                    )
                finally:
                    db.close()
        append_log(state, "db_persist", "node_error", {"error": str(err_msg)})

    try:
        with_db_retry(
            _persist,
            max_retries=MAX_NODE_RETRIES,
            logger=logger,
            on_non_transient=_on_non_transient,
        )
        if state.get("error"):
            append_log(state, "db_persist", "node_error", {"error": state["error"]})
            return state
        append_log(
            state, "db_persist", "node_complete", {"ingest_id": state.get("ingest_id")}
        )
        return state
    except SAOperationalError as e:
        state["error"] = f"DB persist failed after {MAX_NODE_RETRIES} attempts: {e}"
        append_log(state, "db_persist", "node_error", {"error": str(e)})
        return state
    except Exception:
        # _on_non_transient already handled process job failure and logging
        return state


def _db_persist_inner(state: AgentState) -> None:
    """Run the core DB persistence; raises on any error (called inside retry loop)."""
    _run_persist(state)


def _db_persist_inner_with_cleanup(state: AgentState) -> AgentState:
    """Run persistence with full error cleanup; used when retry loop is exhausted/skipped."""
    _run_persist(state)
    return state


def _auto_derive_statements(
    db, company_nit: str, *, ingest_id: str = ""
) -> Optional[bool]:
    """Derive financial statements from all journal entries for the company/period.

    Non-fatal: logs warnings on failure but never raises.
    """
    if not company_nit:
        return None

    period = get_journal_entry_period(db, company_nit=company_nit)
    if period is None:
        logger.warning(
            "[persist] No JournalEntryLines for %s — skipping statement derivation",
            company_nit,
        )
        return None

    min_fecha, max_fecha = period

    # Guard: ensure period values are real datetimes (not Mock objects from tests)
    if not isinstance(min_fecha, datetime) or not isinstance(max_fecha, datetime):
        logger.warning(
            "[persist] Unexpected period type (%s, %s) — skipping derivation",
            type(min_fecha).__name__,
            type(max_fecha).__name__,
        )
        return None

    # Expand the min/max journal dates to full calendar-month boundaries so the
    # derived BG/ER cover the natural accounting period the documents belong to
    # (e.g. a PILA dated 2026-01-06 yields a period 2026-01-01 → 2026-01-31).
    from app.services.date_utils import first_of_month, last_of_month

    period_start = first_of_month(min_fecha)
    period_end = last_of_month(max_fecha)

    logger.info(
        "[persist] Deriving statements for %s (%s -> %s)",
        company_nit,
        period_start.date(),
        period_end.date(),
    )

    try:
        entries = db_service.get_journal_entry_lines(
            db,
            company_nit=company_nit,
            start_date=period_start,
            end_date=period_end,
        )
        mapped = [
            {
                "fecha": e.get("fecha"),
                "cuenta": e.get("cuenta_puc", ""),
                "descripcion": e.get("descripcion", ""),
                "tercero_nit": e.get("tercero_nit", ""),
                "detalle": e.get("descripcion", ""),
                "debito": e.get("debito", "0"),
                "credito": e.get("credito", "0"),
            }
            for e in entries
        ]
        PersistOrchestrator(db).derive_and_persist_statements(
            mapped,
            ingest_id=ingest_id,
            company_nit=company_nit,
            period_start=period_start,
            period_end=period_end,
        )
    except Exception as exc:
        logger.warning("[persist] derive failed (non-fatal): %s", exc, exc_info=True)
        return False
    return True


def _try_via_b_auto_derive(
    db, *, company_nit: str, period_start, period_end
) -> Optional[bool]:
    """After a Via B upload, check if all 3 source docs are present and derive if so.

    Non-fatal: logs but never raises.
    """
    if not company_nit or period_start is None or period_end is None:
        return None

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
        return None

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
        return False
    return True


def _run_persist(state: AgentState) -> AgentState:
    """Core persistence logic. Raises on failure; called by the retry wrappers."""
    mode = state.get("mode", "ingest")
    pathway = state.get("pathway", "build_from_scratch")
    interpreted = state.get("interpreted_data", {}) or {}
    classification = state.get("document_classification") or {}
    doc_type = as_str(classification.get("doc_type"), "")
    contador_output: dict = {}
    company_nit: Optional[str] = None

    # --- Vía B: persist existing financial statement directly ---
    if mode == "ingest" and pathway == "work_with_existing":
        _persist_financial_statement(state)
        return state

    if mode == "process":
        if state.get("force_persist"):
            # User has explicitly chosen to override audit issues — do not run the
            # pre-persist auditor at all (it would pollute state["unfixable_findings"]
            # and surface stale blockers in the success response).
            logger.warning(
                "db_persist: force_persist=True — skipping pre-persist auditor entirely"
            )
            # Strip any blockers carried over from a prior run.
            state["unfixable_findings"] = [
                f
                for f in (state.get("unfixable_findings") or [])
                if not (
                    isinstance(f, dict)
                    and str(f.get("severity", "")).lower() == "blocker"
                )
            ]
        else:
            from app.agents.audit_utils import append_audit_report
            from app.agents.auditors import pre_persist_auditor
            from app.models.audit import AuditFinding, Severity

            pre_persist_report = pre_persist_auditor.run(state)
            append_audit_report(state, pre_persist_report)

            report_blockers = [
                f for f in pre_persist_report.findings if f.severity == Severity.BLOCKER
            ]
            state_blockers = [
                f
                for f in (state.get("unfixable_findings") or [])
                if isinstance(f, dict)
                and str(f.get("severity", "")).lower() == "blocker"
            ]

            if report_blockers or state_blockers:
                from app.agents.audit_utils import record_giveup

                all_blockers = report_blockers or [
                    AuditFinding(**f) if isinstance(f, dict) else f
                    for f in state_blockers
                ]
                first_rule = (
                    all_blockers[0].rule_id if all_blockers else "AUDIT-BLOCKER"
                )
                record_giveup(state, "persist", all_blockers, attempts=1)
                state["current_agent"] = "audit_review_terminal"
                state["needs_hitl_review"] = True
                logger.warning(
                    "db_persist: pre-persist blocker detected — routing to HITL rule_id=%s",
                    first_rule,
                )
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

        # Group asientos by matching debit amounts to raw_tx totals so each
        # transaction gets its own paired journal entries. For single-tx batches
        # (the common case) all asientos belong to that one transaction.
        def _asientos_for_tx(raw_tx: dict, all_asientos: list, used: set) -> list:
            tx_total = safe_decimal(raw_tx.get("total")) or Decimal("0")
            matched: list = []
            # Find a debit+credit pair whose debit amount equals this tx total.
            for a in all_asientos:
                if id(a) in used:
                    continue
                if str(a.get("tipo_movimiento", "")).lower() == "debito":
                    valor = safe_decimal(a.get("valor")) or Decimal("0")
                    if abs(valor - tx_total) < Decimal("1"):
                        matched.append(a)
                        used.add(id(a))
                        # Also grab the matching credit line(s) with the same amount.
                        for b in all_asientos:
                            if id(b) in used:
                                continue
                            if str(b.get("tipo_movimiento", "")).lower() == "credito":
                                bval = safe_decimal(b.get("valor")) or Decimal("0")
                                if abs(bval - tx_total) < Decimal("1"):
                                    matched.append(b)
                                    used.add(id(b))
                                    break
                        break
            has_debit = any(
                str(a.get("tipo_movimiento", "")).lower() == "debito" for a in matched
            )
            has_credit = any(
                str(a.get("tipo_movimiento", "")).lower() == "credito" for a in matched
            )
            if has_debit and has_credit:
                return matched
            return all_asientos  # fallback: balanced fallback when no exact match found

        used_asiento_ids: set = set()
        transactions = []
        for raw_tx in raw_txs if raw_txs else [{}]:
            if not isinstance(raw_tx, dict):
                continue
            tx_asientos = _asientos_for_tx(raw_tx, asientos, used_asiento_ids)
            debit_line = next(
                (
                    a
                    for a in tx_asientos
                    if str(a.get("tipo_movimiento", "")).lower() == "debito"
                ),
                None,
            )
            base_tx = raw_tx
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
            transactions.append(
                {
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
                    "referencias_legales": tributario_output.get(
                        "referencias_legales", []
                    ),
                    "agent_reasoning": (state.get("result") or {}).get(
                        "agent_reasoning"
                    ),
                    "_contador_asientos": tx_asientos,
                }
            )
    else:
        # New rich-schema path: interpreted_data is a typed content dict (FacturaVentaContent, etc.)
        # Build one or multiple tx rows from structured fields.
        if isinstance(interpreted, dict) and "transactions" not in interpreted:
            transactions = build_structured_transactions(interpreted, doc_type)
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

    ingest_id = as_str(state.get("ingest_id"), "")
    db = SessionLocal()
    orchestrator = PersistOrchestrator(db)

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
            ingest_id = as_str(getattr(ingest_job, "id", ""), "")
            state["ingest_id"] = ingest_id

        total_lines = 0
        total_duplicates = 0
        posted_ids: list[str] = []
        pending_ids: list[str] = []

        if mode == "process":
            process_id = as_str(state.get("process_id"), "")
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

        pending_ids_ordered = list(state.get("pending_transaction_ids") or [])
        for tx_idx, tx_data in enumerate(transactions):
            # Parse fecha from the LLM-extracted document; if unparseable, keep
            # ``None`` so the pre-persist auditor can route the ingest to HITL
            # (rule ING-FECHA-MISSING). NEVER fall back to datetime.now() here
            # — that silently mis-dates accounting entries.
            fecha = safe_datetime(tx_data.get("fecha"))
            if fecha is None:
                tx_data.setdefault("needs_user_fecha", True)
            total = safe_decimal(
                tx_data.get("total") or tx_data.get("valor_total")
            ) or Decimal("0")
            nit_emisor = as_str(tx_data.get("nit_emisor"), "").strip()
            nit_receptor = as_str(tx_data.get("nit_receptor"), "").strip()
            company_nit = _resolve_company_nit(state, tx_data)
            if not company_nit:
                logger.warning(
                    "db_persist: company_nit unresolved; persisting transaction "
                    "with NULL tenant for manual triage (ingest_id=%s)",
                    ingest_id,
                )
            if not nit_receptor and company_nit:
                nit_receptor = company_nit
                logger.warning(
                    "db_persist: nit_receptor missing in extracted transaction; using company_nit=%s",
                    company_nit,
                )
            descripcion = as_str(
                tx_data.get("concepto") or tx_data.get("descripcion"), ""
            )
            items = tx_data.get("items") or tx_data.get("detalle_items") or []

            if mode == "process" and (
                state.get("pending_transaction_id") or pending_ids_ordered
            ):
                # Use per-transaction ID from ordered list when available; fall back to
                # the legacy single-ID for backwards compat with single-transaction batches.
                if tx_idx < len(pending_ids_ordered):
                    resolve_pending_id = pending_ids_ordered[tx_idx]
                else:
                    resolve_pending_id = as_str(state.get("pending_transaction_id"), "")
                txn_pending = (
                    db.query(TransactionPending)
                    .filter(TransactionPending.id == resolve_pending_id)
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
                    source_file=tx_data.get("source_file"),
                )
                logger.info(f"db_persist: Created TransactionPending {txn_pending.id}")

            pending_ids.append(as_str(getattr(txn_pending, "id", ""), ""))

            duplicates = []
            if nit_emisor and total and fecha:
                duplicates = db_service.check_duplicates(db, nit_emisor, total, fecha)
                txn_pending_id = as_str(getattr(txn_pending, "id", ""), "")
                duplicates = [
                    d
                    for d in duplicates
                    if as_str(getattr(d, "id", ""), "") != txn_pending_id
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
                cuenta_puc = as_str((debit_line or {}).get("cuenta_puc"), "")
                puc_descripcion = as_str((debit_line or {}).get("nombre_cuenta"), "")
                if not cuenta_puc:
                    msg = "DB persist error: contador output missing debit cuenta_puc"
                    logger.error(msg)
                    state["error"] = msg
                    raise RuntimeError(msg)
            else:
                cuenta_puc = as_str(tx_data.get("cuenta_puc"), "")
                if not cuenta_puc:
                    # No cuenta_puc in extracted data — skip persisting a posted
                    # record in ingest mode. Pipeline 2 (mode=process, after HITL
                    # confirm) will classify via contador and persist the correct
                    # posted. Without this skip we'd create a placeholder posted
                    # with 519595 fallback that duplicates the one Pipeline 2
                    # creates with the proper cuenta — bug P0-1.
                    pathway_value = state.get("pathway") or ""
                    if pathway_value != "work_with_existing":
                        logger.info(
                            "db_persist[ingest]: skipping posted creation — no cuenta_puc "
                            "in extracted data; Pipeline 2 will classify and persist "
                            "(pending_id=%s)",
                            as_str(getattr(txn_pending, "id", "")),
                        )
                        continue
                    # Vía B (work_with_existing) still needs a posted record
                    # even without cuenta_puc — those docs don't go through
                    # the contador pipeline.
                    logger.warning(
                        "db_persist: No PUC code in ingest data — "
                        "defaulting to 519595 (Otros Gastos). "
                        "Run accounting pipeline to classify properly."
                    )
                    cuenta_puc = "519595"
                puc_descripcion = as_str(tx_data.get("cuenta_nombre"), "")

            puc_record = db_service.validate_puc_exists(db, cuenta_puc)
            if not puc_record and len(cuenta_puc) > 4:
                # ERP auxiliary codes (7-12 digits) are company-specific subdivisions.
                # Walk up the hierarchy (6 → 5 → 4 digits) to find the parent account.
                for parent_len in (6, 5, 4):
                    parent_code = cuenta_puc[:parent_len]
                    parent_record = db_service.validate_puc_exists(db, parent_code)
                    if parent_record:
                        logger.info(
                            "db_persist: auxiliary PUC %s → resolved to parent %s",
                            cuenta_puc,
                            parent_code,
                        )
                        cuenta_puc = parent_code
                        puc_record = parent_record
                        break
            if puc_record:
                puc_descripcion = as_str(getattr(puc_record, "nombre", ""), "")
            elif mode == "process":
                if state.get("force_persist"):
                    # User chose to override audit issues — fall back to the
                    # catch-all 519595 instead of failing the whole pipeline.
                    logger.warning(
                        "db_persist: force_persist=True — PUC code %s not found, "
                        "falling back to 519595",
                        cuenta_puc,
                    )
                    cuenta_puc = "519595"
                    fallback = db_service.validate_puc_exists(db, cuenta_puc)
                    puc_descripcion = (
                        as_str(getattr(fallback, "nombre", ""), "")
                        if fallback
                        else "Otros gastos diversos"
                    )
                else:
                    msg = f"DB persist error: PUC code {cuenta_puc} not found"
                    logger.error(msg)
                    state["error"] = msg
                    raise RuntimeError(msg)
            else:
                logger.warning(f"db_persist: PUC code {cuenta_puc} not found")

            retefuente = safe_decimal(tx_data.get("retefuente")) or Decimal("0")
            reteica = safe_decimal(tx_data.get("reteica")) or Decimal("0")
            iva = safe_decimal(
                tx_data.get("iva") or tx_data.get("iva_valor")
            ) or Decimal("0")
            ica = safe_decimal(tx_data.get("ica")) or Decimal("0")
            provision_renta = safe_decimal(tx_data.get("renta")) or Decimal("0")
            neto = safe_decimal(tx_data.get("neto_a_pagar")) or total

            if mode == "process":
                import re as _re

                _raw_asientos = tx_data.get("_contador_asientos", [])
                _sanitized_asientos = []
                _HEADER_TOKENS = {
                    "TERCERO",
                    "CODIGO",
                    "CONCEPTO",
                    "DEBITO",
                    "CREDITO",
                    "CUENTA",
                }
                for _a in _raw_asientos:
                    if not isinstance(_a, dict):
                        continue
                    _puc = str(_a.get("cuenta_puc") or "")
                    if not _re.match(r"^\d{1,12}$", _puc):
                        _desc_token = (
                            str(_a.get("descripcion") or _a.get("concepto") or "")
                            .strip()
                            .upper()
                        )
                        if _desc_token in _HEADER_TOKENS:
                            logger.warning(
                                "db_persist: dropping phantom header-row asiento "
                                "(cuenta_puc=%r, descripcion=%r)",
                                _puc,
                                _desc_token,
                            )
                            continue
                        _a = {
                            **_a,
                            "cuenta_puc": "519595",
                            "nombre_cuenta": "Otros Gastos Diversos",
                        }
                    _sanitized_asientos.append(_a)
                journal_json = JournalBuilder.build_from_contador(
                    fecha=fecha,
                    asientos=_sanitized_asientos,
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
                doc_type_full = as_str(
                    (state.get("document_classification") or {}).get("doc_type"),
                    "",
                )
                journal_json = JournalBuilder.build_from_ingest(
                    fecha=fecha,
                    cuenta_puc=cuenta_puc,
                    puc_descripcion=puc_descripcion,
                    total=total,
                    iva=iva,
                    retefuente=retefuente,
                    reteica=reteica,
                    nit=nit_emisor,
                    descripcion=descripcion,
                    doc_type=doc_type_full,
                    cuenta_reteica=_resolve_cuenta_reteica(state),
                )
                # Ingest path uses a hardcoded factura_compra pattern (cuenta de
                # gasto + 220505 CxP). For bank-outflow doc subtypes the credit
                # side must hit 111005 (Banco), not CxP. Swap 220505 cred ->
                # 111005 to mirror the contador-stage corrector.
                if doc_type_full in {
                    "comprobante_egreso",
                    "extracto_bancario",
                    "conciliacion_bancaria",
                }:
                    for _entry in journal_json:
                        if (
                            as_str(_entry.get("cuenta"), "") == "220505"
                            and safe_decimal(_entry.get("credito"))
                            and safe_decimal(_entry.get("credito")) > Decimal("0")
                        ):
                            logger.info(
                                "db_persist[ingest]: %s 220505 cred -> 111005 — descripcion=%r",
                                doc_type_full,
                                as_str(_entry.get("detalle"), "")[:120],
                            )
                            _entry["cuenta"] = "111005"
                            _entry["descripcion"] = "Bancos Nacionales"

                # Bank statement INFLOW: the default builder produces
                # "DEBIT cuenta + CREDIT 111005" (outflow pattern). For an
                # inflow movement (bank received funds) the asiento must be
                # inverted: DEBIT 111005 + CREDIT cuenta. Swap each entry's
                # debito/credito sides while preserving the cuenta and amounts.
                bank_direction = as_str(tx_data.get("bank_direction"), "")
                if doc_type_full == "extracto_bancario" and bank_direction == "entrada":
                    logger.info(
                        "db_persist[ingest]: inverting extracto entrada asiento — descripcion=%r",
                        (descripcion or "")[:120],
                    )
                    for _entry in journal_json:
                        _d = _entry.get("debito") or "0"
                        _c = _entry.get("credito") or "0"
                        _entry["debito"] = str(_c)
                        _entry["credito"] = str(_d)

                # Specialize 5195/519595 fallback via the keyword corrector
                # when the description identifies a concrete gasto.
                if cuenta_puc in {"5195", "519595"}:
                    from app.agents.contador_puc_corrector import _suggest_puc

                    suggested = _suggest_puc(descripcion or "")
                    if suggested:
                        logger.info(
                            "db_persist[ingest]: rewriting %s -> %s based on descripcion=%r",
                            cuenta_puc,
                            suggested,
                            (descripcion or "")[:120],
                        )
                        for _entry in journal_json:
                            if as_str(_entry.get("cuenta"), "") in {"5195", "519595"}:
                                _entry["cuenta"] = suggested
                        cuenta_puc = suggested
                        suggested_record = db_service.validate_puc_exists(
                            db, cuenta_puc
                        )
                        if suggested_record:
                            puc_descripcion = as_str(
                                getattr(suggested_record, "nombre", ""), ""
                            )

                tax_references = interpreted.get("referencias_legales", [])
                raw_reasoning = tx_data.get("agent_reasoning")
                agent_reasoning = (
                    raw_reasoning if isinstance(raw_reasoning, dict) else {}
                )

            # ── Duplicate-posted guard ────────────────────────────────────────
            # Same source document re-uploaded creates new IngestJob + new
            # TransactionPending but must NOT create a duplicate TransactionPosted.
            # Natural key: (company_nit, nit_emisor, fecha::date, total).
            _existing_posted = None
            if company_nit and nit_emisor and fecha and total:
                try:
                    _existing_posted = db_service.find_duplicate_posted(
                        db,
                        company_nit=company_nit,
                        nit_emisor=nit_emisor,
                        fecha=fecha,
                        total=total,
                    )
                except Exception as _dup_err:
                    logger.warning(
                        "db_persist: duplicate-posted check failed (%s) — proceeding",
                        _dup_err,
                    )

            if _existing_posted is not None:
                existing_posted_id = as_str(getattr(_existing_posted, "id", ""), "")
                logger.warning(
                    "db_persist: duplicate_skipped — TransactionPosted %s already exists "
                    "for company_nit=%s nit_emisor=%s total=%s fecha=%s; "
                    "skipping re-post of re-uploaded document",
                    existing_posted_id,
                    company_nit,
                    nit_emisor,
                    total,
                    fecha,
                )
                state["duplicate_skipped"] = True
                posted_ids.append(existing_posted_id)
                continue

            txn_posted = db_service.create_transaction_posted(
                db,
                transaction_pending_id=as_str(getattr(txn_pending, "id", "")),
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
            posted_ids.append(as_str(getattr(txn_posted, "id", ""), ""))
            logger.info("db_persist: Created TransactionPosted %s", txn_posted.id)

            lines = orchestrator.persist_journal_entries(
                journal_json,
                transaction_posted_id=as_str(getattr(txn_posted, "id", "")),
                company_nit=company_nit or "",
            )
            total_lines += len(lines)
            logger.info("db_persist: Created %d journal entry lines", len(lines))

        auditor_out = state.get("auditor_output") or {}
        classification = state.get("document_classification") or {}
        doc_type = as_str(classification.get("doc_type"), "")
        pathway_value = as_str(state.get("pathway"), "")

        if mode == "ingest":
            db_service.update_ingest_job(
                db,
                ingest_id,
                IngestStatus.COMPLETED,
                document_type=doc_type,
                pathway=pathway_value,
            )
        else:
            process_id = as_str(state.get("process_id"), "")
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
            # Mode=process still owns the ingest_job lifecycle: once persistence
            # finishes the source ingest is conceptually done. Without this the
            # row stays at PROCESSING indefinitely while the UI shows COMPLETADO.
            if ingest_id:
                db_service.update_ingest_job(
                    db,
                    ingest_id,
                    IngestStatus.COMPLETED,
                    document_type=doc_type,
                    pathway=pathway_value,
                )

        # Auto-derive financial statements after process completes (non-fatal)
        if mode == "process" and company_nit:
            derive_result = _auto_derive_statements(
                db, company_nit, ingest_id=ingest_id
            )
            if derive_result is False:
                from app.agents.audit_utils import append_finding
                from app.models.audit import AuditFinding, AuditTarget, Severity

                append_finding(
                    state,
                    AuditFinding(
                        target=AuditTarget.PRE_PERSIST,
                        rule_id="PERS-STATEMENT-DERIVATION-FAIL",
                        severity=Severity.WARNING,
                        fixable=False,
                        responsible_agent="persist",
                        technical_message="Financial statement derivation failed after persist.",
                        user_message_es="No se pudieron generar los estados financieros automáticamente. Puede generarlos manualmente.",
                    ),
                )

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
        raw_error = str(e)
        if raw_error.lstrip().startswith("{"):
            state["error"] = raw_error
        else:
            state["error"] = f"DB persist error: {raw_error}"
        append_log(state, "db_persist", "node_error", {"error": str(e)})

        if mode == "ingest" and ingest_id:
            try:
                db_service.update_ingest_job(
                    db,
                    ingest_id,
                    IngestStatus.FAILED,
                    extraction_errors=[str(e)],
                )
            except Exception as status_err:
                logger.debug(
                    "persist_node: failed to mark ingest job FAILED: %s", status_err
                )

        if mode == "process":
            process_id = as_str(state.get("process_id"), "")
            if process_id:
                try:
                    db_service.update_process_job(
                        db,
                        process_id,
                        status=ProcessStatus.FAILED,
                        current_stage="failed",
                        current_agent="db_persist",
                        error_message=state.get("error") or str(e),
                        progress=100,
                        agent_log_entry={
                            "agent": "db_persist",
                            "stage": "failed",
                            "status": "failed",
                        },
                    )
                except Exception as status_err:
                    logger.debug(
                        "persist_node: failed to mark process job FAILED: %s",
                        status_err,
                    )
    finally:
        db.close()

    return state


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


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

    ingest_id = as_str(state.get("ingest_id"), "")
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
            ingest_id = as_str(getattr(ingest_job, "id", ""), "")
            state["ingest_id"] = ingest_id

        # Populate routing metadata on the job
        if ingest_job:
            ingest_job.document_type = doc_type
            ingest_job.pathway = as_str(state.get("pathway"), "work_with_existing")

        company_nit = _resolve_company_nit(state)
        if company_nit is None:
            raise ValueError(
                "Vía B persistence requires a company NIT (provided or detected)"
            )

        period_start = safe_datetime(
            classification.get("period_start") or interpreted.get("periodo_inicio")
        )
        period_end = safe_datetime(
            classification.get("period_end")
            or interpreted.get("periodo_fin")
            or interpreted.get("periodo_corte")
        )

        if not period_end:
            # Keep persistence backward compatible: if no period is extractable,
            # anchor the statement to current timestamp as period end.
            period_end = datetime.now(timezone.utc)
        if not period_start:
            # Mirror period_end for schema consistency. Correct for point-in-time
            # statements (balance_general); for period statements (estado_resultados,
            # flujo_de_caja) equal start/end is flagged so downstream reviewers
            # notice when the source lacks a proper period.
            logger.warning(
                "db_persist: period_start missing for %s; using period_end as fallback "
                "(statement may lack a valid reporting period)",
                doc_type or "unknown statement type",
            )
            period_start = period_end

        # balance_general_anterior is the same statement type in DB — it's just a
        # balance_general from the prior period. The "anterior" label only exists
        # in the upload UI to guide users; _load_prior_balance finds it by period_end.
        persisted_type = (
            "balance_general" if doc_type == "balance_general_anterior" else doc_type
        )

        # Create FinancialStatement record
        stmt = db_service.create_financial_statement(
            db,
            ingest_id=ingest_id,
            statement_type=persisted_type,
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

        # Vía B derivation is now manual — triggered via POST /api/v1/reports/derivation/run.

        state["db_result"] = {
            "ingest_id": ingest_id,
            "financial_statement_id": stmt.id,
            "statement_type": persisted_type,
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
    concepto = as_str(interpreted.get("concepto"), "").strip()
    if not concepto:
        default_by_type = {
            "extracto_bancario": "Extracto bancario",
            "nomina": "Nomina",
            "recibo_pago_impuesto": "Pago de impuesto",
        }
        concepto = default_by_type.get(doc_type, "")

    items = interpreted.get("items")
    items_count = len(items) if isinstance(items, list) else 0

    preview: dict = {
        "nit_emisor": interpreted.get("nit_emisor"),
        "total": str(interpreted.get("total", "")),
        "fecha": str(interpreted.get("fecha", "")),
        "concepto": concepto[:100],
        "items_count": items_count,
    }

    # Pre-armed journal entry table (CE, RC, Nómina) — must be preserved so
    # downstream contador/tributario nodes can detect and respect the
    # already-booked asiento instead of re-classifying.
    asientos_documento = interpreted.get("asientos_documento")
    if isinstance(asientos_documento, list) and asientos_documento:
        preview["asientos_documento"] = asientos_documento

    return preview
