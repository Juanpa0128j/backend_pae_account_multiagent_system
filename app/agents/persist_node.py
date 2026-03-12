"""
DB Persist node for the LangGraph pipeline.

Supports both pipeline modes:
  - "ingest": single-shot PDF ingestion pipeline
    (supervisor → ingest → validate_output → db_persist)
  - "process": full accounting pipeline
    (process_supervisor → contador → validate_contador
     → auditor → validate_auditor → db_persist)

In process mode the node reads the validated contador_output and
auditor_output from state and stores them alongside the posted
transaction so the full audit trail is persisted.
"""
# type: ignore[assignment]
# SQLAlchemy model attributes are runtime values on instances; static typing
# can mis-infer them as Column[...] in service/pipeline code.

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.agents.state import AgentState
from app.core.database import SessionLocal
from app.services import db_service
from app.models.database import (
    IngestStatus,
    TransactionPending,
    TransactionStatus,
    ProcessStatus,
)

logger = logging.getLogger(__name__)


def _as_str(value: Any, default: str = "") -> str:
    """Normalize possibly-ORM values to plain strings."""
    if value is None:
        return default
    return str(value)


def _safe_decimal(value) -> Optional[Decimal]:
    """Safely convert a value to Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_datetime(value) -> Optional[datetime]:
    """Safely parse a datetime string."""
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


def db_persist_node(state: AgentState) -> AgentState:
    """
    Persist interpreted data to PostgreSQL.

    Ingest mode flow:
    1. Create/update IngestJob
    2. Create TransactionPending from extracted data
    3. Duplicate detection
    4. Classify PUC and create TransactionPosted
    5. Generate JournalEntryLines
    6. Mark IngestJob as completed

    Process mode flow (adds auditor awareness):
    Steps 1-6 as above, but reads pending_transaction_id from state,
    uses contador_output for PUC classification, and embeds
    auditor_output in agent_reasoning on TransactionPosted.
    """
    if state.get("error"):
        logger.warning("db_persist: Skipping due to upstream error: %s", state["error"])
        return state

    mode = state.get("mode", "ingest")
    interpreted = state.get("interpreted_data", {})

    if mode != "process" and not interpreted:
        logger.warning("db_persist: No interpreted_data to persist")
        return state

    ingest_id = _as_str(state.get("ingest_id"), "")
    db = SessionLocal()
    try:
        # ── 1. Create or update IngestJob ──
        if ingest_id:
            ingest_job = db_service.get_ingest_job(db, ingest_id)
            if ingest_job:
                db_service.update_ingest_job(
                    db, ingest_id, IngestStatus.PROCESSING,
                    raw_preview=_build_preview(interpreted),
                )
        else:
            file_name = state.get("file_path", "unknown.pdf").split("/")[-1]
            ingest_job = db_service.create_ingest_job(
                db, file_name, state.get("file_path")
            )
            ingest_id = _as_str(ingest_job.id)
            state["ingest_id"] = ingest_id

        # ── 2. Resolve TransactionPending ──
        txn_pending = None
        fecha = None
        total = None
        nit_emisor = ""
        nit_receptor = ""
        descripcion = ""

        if mode == "process":
            pending_id = state.get("pending_transaction_id")
            if pending_id:
                txn_pending = (
                    db.query(TransactionPending)
                    .filter(TransactionPending.id == pending_id)
                    .first()
                )

            if not txn_pending:
                state["error"] = "db_persist: pending transaction not found for process mode"
                return state

            fecha = _safe_datetime(getattr(txn_pending, "fecha", None))
            total = _safe_decimal(getattr(txn_pending, "total", None))
            nit_emisor = _as_str(getattr(txn_pending, "nit_emisor", ""))
            nit_receptor = _as_str(getattr(txn_pending, "nit_receptor", ""))
            descripcion = _as_str(getattr(txn_pending, "descripcion", ""))
        else:
            # Ingest pipeline: build TransactionPending from interpreted_data
            fecha = _safe_datetime(interpreted.get("fecha"))
            total = _safe_decimal(
                interpreted.get("total") or interpreted.get("valor_total")
            )
            nit_emisor = str(interpreted.get("nit_emisor") or "").strip()
            nit_receptor = str(interpreted.get("nit_receptor") or "").strip()
            descripcion = (
                interpreted.get("concepto") or interpreted.get("descripcion", "")
            )
            items = interpreted.get("items") or interpreted.get("detalle_items", [])

            fecha = fecha or datetime.now(timezone.utc)
            total = total or Decimal("0")

            txn_pending = db_service.create_transaction_pending(
                db,
                ingest_id=ingest_id,
                fecha=fecha,
                nit_emisor=nit_emisor,
                nit_receptor=nit_receptor,
                total=total,
                descripcion=descripcion,
                items=items if isinstance(items, list) else [],
                raw_data=interpreted,
            )
            logger.info("db_persist: Created TransactionPending %s", txn_pending.id)

        # ── 3. Duplicate detection ──
        duplicates = []
        if nit_emisor and total is not None and fecha is not None:
            duplicates = db_service.check_duplicates(db, nit_emisor, total, fecha)
            txn_pending_id = _as_str(getattr(txn_pending, "id", ""))
            duplicates = [
                d
                for d in duplicates
                if _as_str(getattr(d, "id", "")) != txn_pending_id
            ]
            if duplicates:
                logger.warning(
                    "db_persist: Found %d potential duplicates for NIT %s, total=%s",
                    len(duplicates), nit_emisor, total,
                )

        # ── 4. Classify PUC and create TransactionPosted ──
        contador_output = state.get("contador_output", {}) if mode == "process" else interpreted

        if mode == "process":
            asientos = contador_output.get("asientos", [])
            debit_line = next(
                (a for a in asientos if str(a.get("tipo_movimiento", "")).lower() == "debito"),
                None,
            )
            cuenta_puc = _as_str((debit_line or {}).get("cuenta_puc"))
            puc_descripcion = _as_str((debit_line or {}).get("nombre_cuenta"))
            if not cuenta_puc:
                state["error"] = "db_persist: contador output missing debit cuenta_puc"
                return state
        else:
            cuenta_puc = interpreted.get("cuenta_puc", "519595")  # legacy fallback
            puc_descripcion = _as_str(interpreted.get("cuenta_nombre"))

        puc_record = db_service.validate_puc_exists(db, cuenta_puc)
        if puc_record:
            puc_descripcion = _as_str(getattr(puc_record, "nombre", "")) or puc_descripcion
        else:
            if mode == "process":
                state["error"] = f"db_persist: PUC code {cuenta_puc} not found"
                return state
            logger.warning("db_persist: PUC code %s not found, using as-is", cuenta_puc)

        # Tax values
        retefuente = _safe_decimal(interpreted.get("retefuente")) or Decimal("0")
        reteica = _safe_decimal(interpreted.get("reteica")) or Decimal("0")
        iva = (
            _safe_decimal(interpreted.get("iva") or interpreted.get("iva_valor"))
            or Decimal("0")
        )
        neto = _safe_decimal(interpreted.get("neto_a_pagar")) or (total or Decimal("0"))

        # Build journal entries JSON
        if mode == "process":
            journal_json = _journal_entries_from_contador(
                fecha=fecha or datetime.now(timezone.utc),
                asientos=contador_output.get("asientos", []),
                nit=nit_emisor,
                descripcion=descripcion,
            )
            neto = total or Decimal("0")
        else:
            journal_json = _build_journal_entries(
                fecha=fecha or datetime.now(timezone.utc),
                cuenta_puc=cuenta_puc,
                puc_descripcion=puc_descripcion,
                total=total or Decimal("0"),
                iva=iva,
                retefuente=retefuente,
                reteica=reteica,
                nit=nit_emisor,
                descripcion=descripcion,
            )

        # Auditor output is available only in process mode
        auditor_out = state.get("auditor_output") or {}

        txn_posted = db_service.create_transaction_posted(
            db,
            transaction_pending_id=_as_str(getattr(txn_pending, "id", "")),
            cuenta_puc=cuenta_puc,
            puc_descripcion=puc_descripcion,
            retefuente=retefuente,
            reteica=reteica,
            iva=iva,
            neto_a_pagar=neto,
            journal_entries_json=journal_json,
            tax_references=interpreted.get("referencias_legales", []),
            agent_reasoning={
                "contador": (contador_output if mode == "process" else {}),
                "auditor": auditor_out,
            },
        )
        logger.info("db_persist: Created TransactionPosted %s", txn_posted.id)

        # ── 5. Create normalized JournalEntryLines ──
        lines = db_service.create_journal_entry_lines(
            db, _as_str(getattr(txn_posted, "id", "")), journal_json
        )
        logger.info("db_persist: Created %d journal entry lines", len(lines))

        # ── 6. Mark pipeline job as completed ──
        if mode == "ingest":
            db_service.update_ingest_job(db, ingest_id, IngestStatus.COMPLETED)
        process_id = _as_str(state.get("process_id"), "")
        if mode == "process" and process_id:
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

        # ── 7. Enrich state result ──
        state["db_result"] = {
            "ingest_id": ingest_id,
            "transaction_pending_id": _as_str(getattr(txn_pending, "id", "")),
            "transaction_posted_id": _as_str(getattr(txn_posted, "id", "")),
            "journal_lines_count": len(lines),
            "duplicates_found": len(duplicates),
            "cuenta_puc": cuenta_puc,
            "puc_descripcion": puc_descripcion,
            "audit_approved": state.get("audit_approved"),
            "audit_nivel_riesgo": auditor_out.get("nivel_riesgo"),
            "audit_puntaje_calidad": auditor_out.get("puntaje_calidad"),
            "audit_hallazgos_count": len(auditor_out.get("hallazgos", [])),
        }

        if not state.get("result"):
            state["result"] = {}
        state["result"]["db_persisted"] = True
        state["result"]["ingest_id"] = ingest_id
        state["result"]["transaction_id"] = _as_str(getattr(txn_posted, "id", ""))
        state["result"]["audit_approved"] = state.get("audit_approved")
        state["result"]["audit_nivel_riesgo"] = auditor_out.get("nivel_riesgo")

        logger.info(
            "db_persist: Successfully persisted all data for ingest %s", ingest_id
        )

    except Exception as exc:
        logger.error("db_persist: Error persisting data: %s", exc, exc_info=True)
        state["error"] = f"db_persist error: {exc}"

        if ingest_id and mode == "ingest":
            try:
                db_service.update_ingest_job(
                    db, ingest_id, IngestStatus.FAILED,
                    extraction_errors=[str(exc)],
                )
            except Exception:
                pass

        process_id = _as_str(state.get("process_id"), "")
        if process_id and mode == "process":
            try:
                db_service.update_process_job(
                    db,
                    process_id,
                    status=ProcessStatus.FAILED,
                    current_stage="failed",
                    current_agent="db_persist",
                    error_message=str(exc),
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
    *,
    fecha: datetime,
    asientos: list,
    nit: str,
    descripcion: str,
) -> list:
    """Convert ContadorOutput.asientos to journal_entries_json format."""
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


def _build_preview(interpreted: dict) -> dict:
    """Build a quick preview dict from interpreted data."""
    return {
        "nit_emisor": interpreted.get("nit_emisor"),
        "total": str(interpreted.get("total", "")),
        "fecha": str(interpreted.get("fecha", "")),
        "concepto": str(interpreted.get("concepto") or "")[:100],
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
        entries.append({
            "fecha": fecha_iso,
            "cuenta": cuenta_puc,
            "descripcion": puc_descripcion or descripcion,
            "tercero_nit": nit,
            "detalle": descripcion,
            "debito": str(base),
            "credito": "0",
        })

    if iva > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": "240802",
            "descripcion": "IVA Descontable",
            "tercero_nit": nit,
            "detalle": f"IVA por {descripcion}",
            "debito": str(iva),
            "credito": "0",
        })

    total_credito_proveedor = total - retefuente - reteica
    if total_credito_proveedor > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": "220505",
            "descripcion": "Proveedores Nacionales",
            "tercero_nit": nit,
            "detalle": f"CxP {descripcion}",
            "debito": "0",
            "credito": str(total_credito_proveedor),
        })

    if retefuente > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": "240815",
            "descripcion": "Retención en la Fuente - Servicios",
            "tercero_nit": nit,
            "detalle": f"Retefuente {descripcion}",
            "debito": "0",
            "credito": str(retefuente),
        })

    if reteica > 0:
        entries.append({
            "fecha": fecha_iso,
            "cuenta": "236540",
            "descripcion": "ReteICA por pagar",
            "tercero_nit": nit,
            "detalle": f"ReteICA {descripcion}",
            "debito": "0",
            "credito": str(reteica),
        })

    return entries
