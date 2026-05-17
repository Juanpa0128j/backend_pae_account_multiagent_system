"""
Supervisor and validation nodes for ingest and process graphs.

The Supervisor is a finite state machine (FSM) that routes between all
architecture agents based on state['mode'] and state['current_agent']:

  mode == "ingest"   → validate file → ingesta
  mode == "process"  → contador → tributario → auditor → db_persist
  mode == "reporting"→ reportero

All routing decisions and validation outcomes are recorded in agent_log.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.agents.agent_utils import append_log
from app.agents.ingest_agent import _build_llama_parse_kwargs
from app.agents.state import AgentState
from app.agents.audit_utils import build_pinpointed_prompt, record_giveup
from app.agents.validation_rules import (
    GLOBAL_AUDIT_FAILURES,
    MAX_AUDITOR_RETRIES,
    MAX_CONTADOR_RETRIES,
    RETRY_BUDGETS,
    _hydrate_contador_account_names,
    _missing_puc_codes,
    _normalize_contador_puc_codes,
    _resolve_puc_code,
    validate_auditor_output_node,
    validate_contador_output_node,
    validate_output_node,
)
from app.models.audit import AuditFinding, Severity
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.database import IngestStatus
from app.services import db_service
from app.services.nit_utils import normalize_optional_nit
from app.services.validation_engine import ValidationResult, get_validator

logger = get_logger("app.agents.supervisor")

# Backward-compat re-exports so existing imports keep working.
__all__ = [
    "MAX_AUDITOR_RETRIES",
    "MAX_CONTADOR_RETRIES",
    "_hydrate_contador_account_names",
    "_missing_puc_codes",
    "_normalize_contador_puc_codes",
    "_resolve_puc_code",
    "validate_auditor_output_node",
    "validate_contador_output_node",
    "validate_output_node",
]


def _normalize_tributario_output(state: AgentState, tributario_output: dict) -> dict:
    """Best-effort normalization for TributarioOutput before strict schema validation."""
    if not isinstance(tributario_output, dict):
        tributario_output = {}

    normalized = dict(tributario_output)

    impuestos = normalized.get("impuestos")
    if not isinstance(impuestos, list):
        impuestos = []

    calculated_total = Decimal("0")
    for imp in impuestos:
        if not isinstance(imp, dict):
            continue
        try:
            calculated_total += Decimal(str(imp.get("valor_impuesto", 0) or 0))
        except Exception:
            continue

    aplica_impuestos = bool(impuestos)

    total_impuestos = normalized.get("total_impuestos")
    if total_impuestos is None:
        total_impuestos = str(calculated_total)

    documento_referencia = str(normalized.get("documento_referencia") or "").strip()
    if not documento_referencia:
        contador_output = state.get("contador_output") or {}
        documento_referencia = str(
            contador_output.get("descripcion_general") or ""
        ).strip()
    if not documento_referencia:
        raw_txs = state.get("raw_transactions") or []
        if isinstance(raw_txs, list) and raw_txs:
            first_tx = raw_txs[0] if isinstance(raw_txs[0], dict) else {}
            documento_referencia = str(
                first_tx.get("referencia")
                or first_tx.get("descripcion")
                or "sin referencia"
            ).strip()

    referencias_legales = normalized.get("referencias_legales")
    if not isinstance(referencias_legales, list):
        referencias_legales = []

    asientos_enriquecidos = normalized.get("asientos_enriquecidos")
    if not isinstance(asientos_enriquecidos, list):
        contador_output = state.get("contador_output") or {}
        asientos_enriquecidos = (
            contador_output.get("asientos") if isinstance(contador_output, dict) else []
        )
    if not isinstance(asientos_enriquecidos, list):
        asientos_enriquecidos = []

    normalized["fecha_analisis"] = (
        normalized.get("fecha_analisis") or date.today().isoformat()
    )
    normalized["documento_referencia"] = documento_referencia or "sin referencia"
    normalized["impuestos"] = impuestos
    normalized["aplica_impuestos"] = aplica_impuestos
    normalized["total_impuestos"] = str(total_impuestos)
    normalized["observaciones"] = normalized.get("observaciones")
    normalized["referencias_legales"] = referencias_legales
    normalized["asientos_enriquecidos"] = asientos_enriquecidos

    return normalized


# ---------------------------------------------------------------------------
# Pipeline 1 supervisor — ingest graph entry point
# ---------------------------------------------------------------------------


def supervisor_node(state: AgentState) -> AgentState:
    """
    Ingest supervisor: validates file input and routes to ingest worker.
    Also handles re-entry from the unified graph after agent completions.
    """
    # Initialise fields that may be missing
    for field, default in [
        ("validation_history", []),
        ("current_agent", ""),
        ("retry_count", 0),
        ("correction_feedback", None),
        ("agent_log", []),
        ("audit_decision", None),
        ("audit_feedback", None),
        ("audit_rejection_count", 0),
    ]:
        if state.get(field) is None:
            state[field] = default

    mode = state.get("mode", "ingest")
    current = state.get("current_agent", "")

    append_log(
        state,
        "supervisor",
        "routing_start",
        {
            "mode": mode,
            "current_agent": current,
        },
    )

    # ------------------------------------------------------------------
    # Ingest pipeline: file upload → ingesta → validate → db_persist
    # ------------------------------------------------------------------
    if mode in ("ingest", "") and not current:
        file_path = state.get("file_path", "")

        if not Path(file_path).exists():
            state["error"] = f"File not found: {file_path}"
            logger.error(state["error"])
            append_log(
                state,
                "supervisor",
                "routing_error",
                {
                    "reason": "file_not_found",
                    "file_path": file_path,
                },
            )
            return state

        _SUPPORTED_EXTENSIONS = (".pdf", ".xlsx", ".xml", ".jpg", ".jpeg", ".png")
        if not any(file_path.lower().endswith(ext) for ext in _SUPPORTED_EXTENSIONS):
            state["error"] = (
                f"Unsupported file type. Accepted: PDF, Excel, XML, JPG, PNG. Got: {file_path}"
            )
            logger.error(state["error"])
            append_log(
                state,
                "supervisor",
                "routing_error",
                {
                    "reason": "unsupported_format",
                    "file_path": file_path,
                },
            )
            return state

        # --- Extract text preview and classify document ---
        ext = Path(file_path).suffix.lower()
        file_name_lower = Path(file_path).name.lower()
        text_preview = ""
        try:
            if ext == ".xlsx":
                from app.services.excel_parser import parse_excel

                markdown_text, tabular_data = parse_excel(file_path)
                state["raw_text"] = markdown_text
                state["parsed_content"] = tabular_data
                text_preview = markdown_text[:3000]
            elif ext == ".xml":
                from app.services.xml_parser import parse_xml

                xml_text = parse_xml(file_path)
                state["raw_text"] = xml_text
                text_preview = xml_text[:3000]
            elif ext == ".pdf":
                from app.services.pdf_processor import extract_text_from_pdf

                text_preview = extract_text_from_pdf(file_path)[:3000]
            elif ext in (".jpg", ".jpeg", ".png"):
                from llama_parse import LlamaParse  # type: ignore[import-untyped]

                from app.core.config import get_settings

                settings = get_settings()
                parser_mode = state.get("parser_mode", "fast")
                parser = LlamaParse(
                    **_build_llama_parse_kwargs(
                        parser_mode,
                        settings.llama_cloud_api_key,
                    )
                )
                documents = parser.load_data(file_path)
                image_text = "\n\n".join([doc.text for doc in documents])

                if not image_text.strip():
                    logger.warning(
                        "Supervisor: empty image preview in markdown mode; retrying with text mode"
                    )
                    fallback_kwargs = _build_llama_parse_kwargs(
                        parser_mode,
                        settings.llama_cloud_api_key,
                    )
                    fallback_kwargs["result_type"] = "text"
                    parser = LlamaParse(**fallback_kwargs)
                    documents = parser.load_data(file_path)
                    image_text = "\n\n".join([doc.text for doc in documents])

                text_preview = image_text[:3000]
        except Exception as preview_err:
            logger.warning(
                "Supervisor: text preview extraction failed: %s", preview_err
            )

        # Classify document by content using LLM (unless already confirmed)
        classification = None
        classification_dict = None
        ingest_job = None
        ingest_id = str(state.get("ingest_id") or "").strip()
        try:
            from app.models.document_types import (
                DocumentType,
                IngestPathway,
                get_pathway,
            )
            from app.services.doc_classifier import classify_document

            if ingest_id:
                db = SessionLocal()
                try:
                    ingest_job = db_service.get_ingest_job(db, ingest_id)
                finally:
                    db.close()

            use_confirmed = bool(
                ingest_job and getattr(ingest_job, "classification_confirmed", False)
            )

            if use_confirmed:
                doc_type_value = str(ingest_job.document_type or "").strip()
                pathway_value = str(ingest_job.pathway or "").strip()
                if doc_type_value and not pathway_value:
                    try:
                        pathway_value = get_pathway(DocumentType(doc_type_value)).value
                    except ValueError:
                        logger.warning(
                            "Supervisor: invalid confirmed doc_type '%s' — falling back to classification",
                            doc_type_value,
                        )
                        use_confirmed = False
                if use_confirmed:
                    classification_dict = {"doc_type": doc_type_value}
                    if pathway_value:
                        classification_dict["pathway"] = pathway_value
                    state["document_classification"] = classification_dict
                    if pathway_value:
                        state["pathway"] = pathway_value

            if not use_confirmed:
                classification = classify_document(
                    text_preview=text_preview,
                    source_format=ext.lstrip("."),
                )
                # `getattr` guards mocks that don't define the attribute; explicit
                # str check avoids treating MagicMock auto-attrs as truthy.
                classification_error = getattr(classification, "error", None)
                if isinstance(classification_error, str) and classification_error:
                    # LLM classification failed and returned a fallback. Surface
                    # the error instead of silently routing as OTRO/build_from_scratch.
                    state["error"] = (
                        "No fue posible clasificar el documento automáticamente. "
                        "Reintente en unos minutos o seleccione el tipo manualmente."
                    )
                    append_log(
                        state,
                        "supervisor",
                        "routing_error",
                        {
                            "reason": "classification_failed",
                            "technical": classification.error,
                        },
                    )
                    return state
                classification_dict = classification.model_dump(mode="json")
                classification_dict["entity_nit"] = normalize_optional_nit(
                    classification_dict.get("entity_nit")
                )
                # If the caller explicitly provided a company_nit, use it instead of
                # the NIT auto-detected from the document content.
                if state.get("company_nit"):
                    override_nit = normalize_optional_nit(state.get("company_nit"))
                    if not override_nit:
                        state["error"] = (
                            "Supervisor: provided company_nit is empty after normalization"
                        )
                        append_log(
                            state,
                            "supervisor",
                            "routing_error",
                            {
                                "reason": "invalid_company_nit",
                            },
                        )
                        return state
                    classification_dict["entity_nit"] = override_nit
                    logger.info(
                        "Supervisor: company_nit override applied — using %s instead of auto-detected %s",
                        override_nit,
                        classification.entity_nit,
                    )
                state["document_classification"] = classification_dict
                state["pathway"] = classification.pathway.value

                # Persist classification metadata early so it remains visible in
                # GET /api/v1/ingest/{id} even if downstream ingest fails.
                if ingest_id:
                    db = SessionLocal()
                    try:
                        ingest_job = db_service.get_ingest_job(db, ingest_id)
                        if ingest_job:
                            current_status = ingest_job.status
                            if not isinstance(current_status, IngestStatus):
                                current_status = IngestStatus(str(current_status))
                            db_service.update_ingest_job(
                                db,
                                ingest_id,
                                current_status,
                                document_type=classification.doc_type.value,
                                pathway=classification.pathway.value,
                                classification_confidence=Decimal(
                                    str(classification.confidence)
                                ),
                            )
                    except Exception as persist_meta_err:
                        logger.warning(
                            "Supervisor: failed to persist classification metadata: %s",
                            persist_meta_err,
                        )
                    finally:
                        db.close()

                append_log(
                    state,
                    "supervisor",
                    "document_classified",
                    {
                        "doc_type": classification.doc_type.value,
                        "pathway": classification.pathway.value,
                        "confidence": classification.confidence,
                    },
                )

            # Lock pathway immediately upon confirmed classification so subsequent
            # uploads are blocked before the pipeline even starts.
            if use_confirmed and state.get("company_nit") and state.get("pathway"):
                _lock_nit = normalize_optional_nit(state["company_nit"])
                if _lock_nit:
                    db = SessionLocal()
                    try:
                        db_service.set_company_locked_pathway(
                            db, _lock_nit, state["pathway"]
                        )
                    except Exception as lock_err:
                        logger.warning("Supervisor: pathway lock failed: %s", lock_err)
                    finally:
                        db.close()

            resolved_doc_type = None
            resolved_pathway = state.get("pathway")
            if classification_dict:
                resolved_doc_type = classification_dict.get("doc_type")

            # Pause for user confirmation on all unconfirmed uploads.
            # Vía B uploads skip this gate because the frontend passes doc_type
            # explicitly at upload time, which sets classification_confirmed=True.
            if ingest_job and not use_confirmed:
                db = SessionLocal()
                try:
                    db_service.update_ingest_job(
                        db,
                        ingest_id,
                        IngestStatus.PENDING_REVIEW,
                        document_type=resolved_doc_type,
                        pathway=state.get("pathway"),
                        classification_confirmed=False,
                        classification_confidence=(
                            Decimal(str(classification.confidence))
                            if classification
                            else None
                        ),
                    )
                except Exception as persist_review_err:
                    logger.warning(
                        "Supervisor: failed to mark ingest pending_review: %s",
                        persist_review_err,
                    )
                finally:
                    db.close()

                state["current_agent"] = "review_terminal"
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {
                        "next_agent": "review_terminal",
                        "mode": "ingest",
                        "status": "pending_review",
                    },
                )
                return state

            # Reached only when classification is confirmed by the user.
            if resolved_pathway == IngestPathway.WORK_WITH_EXISTING.value:
                if (
                    ext == ".xlsx"
                    and "extracto" in file_name_lower
                    and "iva" not in file_name_lower
                ):
                    logger.warning(
                        "Supervisor: forcing build_from_scratch for potential bank statement file %s",
                        file_path,
                    )
                    state["pathway"] = IngestPathway.BUILD_FROM_SCRATCH.value
                    if classification_dict is not None:
                        classification_dict["doc_type"] = (
                            DocumentType.EXTRACTO_BANCARIO.value
                        )
                        state["document_classification"] = classification_dict
                else:
                    via_b_doc_types = {
                        DocumentType.BALANCE_GENERAL,
                        DocumentType.BALANCE_GENERAL_ANTERIOR,
                        DocumentType.ESTADO_RESULTADOS,
                        DocumentType.LIBRO_AUXILIAR,
                        DocumentType.FLUJO_DE_CAJA,
                        DocumentType.CAMBIOS_PATRIMONIO,
                        DocumentType.NOTAS_ESTADOS_FINANCIEROS,
                        DocumentType.LIBRO_DIARIO,
                    }
                    try:
                        resolved_doc_enum = (
                            DocumentType(resolved_doc_type)
                            if resolved_doc_type
                            else None
                        )
                    except ValueError:
                        resolved_doc_enum = None

                    if resolved_doc_enum in via_b_doc_types:
                        state["mode"] = "ingest"
                        # Vía B still needs typed extraction before persistence.
                        # Route through ingesta so interpreted_data is populated,
                        # then db_persist stores it as financial_statement.
                        state["current_agent"] = "ingesta"
                        logger.info(
                            "Supervisor: Vía B — routing to ingesta for %s (%s)",
                            file_path,
                            resolved_doc_type,
                        )
                        append_log(
                            state,
                            "supervisor",
                            "routing_complete",
                            {
                                "next_agent": "ingesta",
                                "mode": "ingest",
                                "pathway": "work_with_existing",
                            },
                        )
                        return state

                    logger.warning(
                        "Supervisor: classifier returned work_with_existing for source doc_type=%s; forcing build_from_scratch",
                        resolved_doc_type,
                    )
                    state["pathway"] = IngestPathway.BUILD_FROM_SCRATCH.value
        except Exception as classify_err:
            logger.warning(
                "Supervisor: document classification failed (continuing with default): %s",
                classify_err,
            )
            state["pathway"] = "build_from_scratch"

        # Vía A (default) — route to ingesta
        state["mode"] = "ingest"
        state["current_agent"] = "ingesta"
        logger.info(f"Supervisor: routing to ingesta for {file_path}")
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": "ingesta",
                "mode": "ingest",
            },
        )
        return state

    # ------------------------------------------------------------------
    # Process pipeline: staged transactions → contador → tributario
    #                   → auditor → db_persist
    # Re-entry after each agent sets current_agent and returns here.
    # ------------------------------------------------------------------
    if mode == "process":
        if not current:
            # Pipeline start — validate input exists
            raw_txs = state.get("raw_transactions", [])
            if not raw_txs:
                state["error"] = "Process supervisor: no staged transactions to process"
                logger.error(state["error"])
                append_log(
                    state,
                    "supervisor",
                    "routing_error",
                    {
                        "reason": "no_transactions",
                    },
                )
                return state
            state["current_agent"] = "contador"
            state["current_stage"] = "routing"
            append_log(
                state,
                "supervisor",
                "routing_complete",
                {
                    "next_agent": "contador",
                    "mode": "process",
                },
            )
            return state

        if current == "contador":
            # Validate contador output before proceeding to tributario
            state = validate_contador_output_node(state)
            if state.get("correction_feedback"):
                # Validation failed — retry contador
                state["current_agent"] = "contador"
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {
                        "next_agent": "contador",
                        "reason": "validation_failed",
                    },
                )
            elif state.get("current_agent") == "audit_review_terminal":
                # Validation exhausted — routed to HITL, leave current_agent as-is
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {
                        "next_agent": "audit_review_terminal",
                        "reason": "contador_validation_exhausted_hitl",
                    },
                )
            elif state.get("error"):
                # Non-retriable error — terminal
                state["current_agent"] = ""
                append_log(
                    state,
                    "supervisor",
                    "routing_error",
                    {
                        "reason": "contador_validation_exhausted",
                    },
                )
            else:
                state["current_agent"] = "tributario"
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {
                        "next_agent": "tributario",
                    },
                )
            return state

        if current == "tributario":
            # Validate TributarioOutput schema before advancing to auditor
            tributario_out = _normalize_tributario_output(
                state,
                state.get("tributario_output", {}),
            )
            validator = get_validator()
            result: ValidationResult = validator.validate(
                "tributario", tributario_out, attempt=1
            )
            if result.is_valid:
                logger.info("Supervisor: tributario output VALID — routing to auditor")
                if result.validated_output:
                    state["tributario_output"] = result.validated_output.model_dump(
                        mode="json"
                    )
                else:
                    state["tributario_output"] = tributario_out
                state["current_agent"] = "auditor"
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {
                        "next_agent": "auditor",
                    },
                )
            else:
                logger.error(
                    f"Supervisor: tributario output INVALID — {result.error_summary()}"
                )
                state["error"] = (
                    f"Tributario output schema validation failed: "
                    f"{result.error_summary()}"
                )
                state["current_agent"] = ""
                append_log(
                    state,
                    "supervisor",
                    "routing_error",
                    {
                        "reason": "tributario_validation_failed",
                        "errors": result.errors[:3],
                    },
                )
            return state

        if current == "auditor":
            # If user confirmed force-persist, skip audit and go straight to db_persist.
            if state.get("force_persist"):
                state["current_agent"] = "db_persist"
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {"next_agent": "db_persist", "reason": "force_persist"},
                )
                return state

            # Validate AuditorOutput schema before deciding whether to persist.
            state = validate_auditor_output_node(state)
            if state.get("correction_feedback"):
                state["current_agent"] = "auditor"
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {
                        "next_agent": "auditor",
                        "reason": "validation_failed",
                    },
                )
            elif state.get("error"):
                state["current_agent"] = ""
                append_log(
                    state,
                    "supervisor",
                    "routing_error",
                    {
                        "reason": "auditor_validation_exhausted",
                    },
                )
            elif state.get("audit_approved") is False:
                # --- Phase 4: pinpointed self-improvement loop ---
                reports = state.get("audit_reports") or []
                last_report = reports[-1] if reports else {}
                raw_findings = (
                    last_report.get("findings", [])
                    if isinstance(last_report, dict)
                    else []
                )
                findings = [
                    AuditFinding(**f) for f in raw_findings if isinstance(f, dict)
                ]

                fixable = [
                    f
                    for f in findings
                    if f.fixable and f.severity in {Severity.ERROR, Severity.BLOCKER}
                ]
                blockers = [
                    f
                    for f in findings
                    if not f.fixable and f.severity == Severity.BLOCKER
                ]

                if blockers:
                    # Unfixable BLOCKER — cannot auto-recover, pause for HITL review.
                    if state.get("unfixable_findings") is None:
                        state["unfixable_findings"] = []
                    state["unfixable_findings"].extend(
                        [b.model_dump() for b in blockers]
                    )
                    rule_ids = [b.rule_id for b in blockers]
                    state["error"] = f"Unfixable audit blockers: {rule_ids}"
                    record_giveup(state, "contador", blockers)
                    state["current_agent"] = "audit_review_terminal"
                    logger.error(
                        "Supervisor: Unfixable audit blockers detected — %s", rule_ids
                    )
                    append_log(
                        state,
                        "supervisor",
                        "routing_complete",
                        {
                            "next_agent": "audit_review_terminal",
                            "reason": "unfixable_blockers",
                            "rule_ids": rule_ids,
                        },
                    )

                elif not fixable:
                    # LLM-level rejection without deterministic findings — fall back to
                    # contador re-route with per-agent retry budget + global cap.
                    rejection_count = state.get("audit_rejection_count", 0) + 1
                    state["audit_rejection_count"] = rejection_count
                    retry_budget = state.get("retry_budget") or {}
                    if "contador" not in retry_budget:
                        retry_budget["contador"] = RETRY_BUDGETS.get("contador", 1)
                    retry_budget["contador"] -= 1
                    state["retry_budget"] = retry_budget

                    global_failures = sum(
                        1 for r in reports if not r.get("approved", True)
                    )
                    if (
                        retry_budget["contador"] < 0
                        or global_failures >= GLOBAL_AUDIT_FAILURES
                        or rejection_count > MAX_AUDITOR_RETRIES
                    ):
                        record_giveup(state, "contador", [])
                        state["current_agent"] = "audit_review_terminal"
                        logger.warning(
                            "Supervisor: Audit give-up (no fixable findings), remaining_budget=%d rejection_count=%d",
                            retry_budget["contador"],
                            rejection_count,
                        )
                        append_log(
                            state,
                            "supervisor",
                            "routing_complete",
                            {
                                "next_agent": "audit_review_terminal",
                                "reason": "audit_giveup_no_fixable_findings",
                                "rejection_count": rejection_count,
                                "remaining_budget": retry_budget["contador"],
                            },
                        )
                    else:
                        state["current_agent"] = "contador"
                        state["correction_feedback"] = (
                            state.get("audit_rejection_reason")
                            or state.get("audit_feedback")
                            or "Audit rejected - please reclassify"
                        )
                        logger.warning(
                            "Supervisor: Auditor rejected (LLM) — re-routing to Contador (remaining budget=%d)",
                            retry_budget["contador"],
                        )
                        append_log(
                            state,
                            "supervisor",
                            "routing_complete",
                            {
                                "next_agent": "contador",
                                "reason": "audit_rejected_llm",
                                "rejection_count": rejection_count,
                                "remaining_budget": retry_budget["contador"],
                            },
                        )

                else:
                    # Deterministic fixable findings — route to the responsible agent.
                    target = fixable[0].responsible_agent
                    routing_target = {
                        "ingest": "ingesta",
                        "persist": "db_persist",
                    }.get(target, target)
                    retry_budget = state.get("retry_budget") or {}
                    if target not in retry_budget:
                        retry_budget[target] = RETRY_BUDGETS.get(target, 1)
                    retry_budget[target] -= 1
                    state["retry_budget"] = retry_budget

                    global_failures = sum(
                        1 for r in reports if not r.get("approved", True)
                    )
                    if (
                        retry_budget[target] < 0
                        or global_failures >= GLOBAL_AUDIT_FAILURES
                    ):
                        record_giveup(state, target, fixable)
                        state["current_agent"] = "audit_review_terminal"
                        logger.warning(
                            "Supervisor: Retry budget exhausted for target=%s global_failures=%d",
                            target,
                            global_failures,
                        )
                        append_log(
                            state,
                            "supervisor",
                            "routing_complete",
                            {
                                "next_agent": "audit_review_terminal",
                                "reason": "retry_budget_exhausted",
                                "target": target,
                                "remaining_budget": retry_budget[target],
                                "global_failures": global_failures,
                            },
                        )
                    else:
                        state["correction_feedback"] = build_pinpointed_prompt(fixable)
                        state["current_agent"] = routing_target
                        logger.warning(
                            "Supervisor: Routing to %s for self-correction (budget=%d)",
                            routing_target,
                            retry_budget[target],
                        )
                        append_log(
                            state,
                            "supervisor",
                            "routing_complete",
                            {
                                "next_agent": routing_target,
                                "reason": "audit_pinpointed_retry",
                                "rule_ids": [f.rule_id for f in fixable],
                                "responsible_agent": target,
                                "remaining_budget": retry_budget[target],
                            },
                        )
            else:
                state["current_agent"] = "db_persist"
                append_log(
                    state,
                    "supervisor",
                    "routing_complete",
                    {
                        "next_agent": "db_persist",
                        "decision": "approved",
                    },
                )
            return state

    # ------------------------------------------------------------------
    # Reporting pipeline
    # ------------------------------------------------------------------
    if mode == "reporting":
        state["current_agent"] = "reportero"
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {
                "next_agent": "reportero",
                "mode": "reporting",
            },
        )
        return state

    # ------------------------------------------------------------------
    # Unknown state — fail gracefully
    # ------------------------------------------------------------------
    state["error"] = f"Supervisor: unknown mode '{mode}' / current_agent '{current}'"
    logger.error(state["error"])
    append_log(
        state,
        "supervisor",
        "routing_error",
        {
            "reason": "unknown_state",
            "mode": mode,
            "current_agent": current,
        },
    )
    return state


# ---------------------------------------------------------------------------
# Process pipeline supervisor — kept for backward-compat with create_process_graph
# ---------------------------------------------------------------------------


def process_supervisor_node(state: AgentState) -> AgentState:
    """Process supervisor: validates staged input and routes to contador worker."""
    if not state.get("validation_history"):
        state["validation_history"] = []
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if state.get("correction_feedback") is None:
        state["correction_feedback"] = None
    if state.get("agent_log") is None:
        state["agent_log"] = []

    raw_txs = state.get("raw_transactions", [])
    if not raw_txs:
        state["error"] = "Process supervisor: no staged transactions to process"
        append_log(state, "supervisor", "routing_error", {"reason": "no_transactions"})
        return state

    state["mode"] = "process"
    state["current_agent"] = "contador"
    state["current_stage"] = "routing"
    append_log(state, "supervisor", "routing_complete", {"next_agent": "contador"})
    return state


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------


def should_retry_agent(state: AgentState) -> str:
    """Conditional edge for ingest graph: retry, error bypass, or proceed."""
    if state.get("error"):
        return "error"
    if state.get("correction_feedback"):
        return "retry"
    return "end"


def should_retry_contador(state: AgentState) -> str:
    """Conditional edge for contador retries in the process graph."""
    if (
        state.get("correction_feedback")
        and state.get("retry_count", 0) < MAX_CONTADOR_RETRIES
    ):
        return "retry"
    return "end"


def should_retry_auditor(state: AgentState) -> str:
    """Conditional edge for auditor retries in the process graph."""
    if (
        state.get("correction_feedback")
        and state.get("retry_count", 0) < MAX_AUDITOR_RETRIES
    ):
        return "retry"
    return "end"


# ---------------------------------------------------------------------------
# Error terminal — unified graph
# ---------------------------------------------------------------------------


def error_terminal_node(state: AgentState) -> AgentState:
    """
    Terminal node for unrecoverable errors detected before pipeline starts.
    Ensures result always has a consistent {status: error} shape.
    """
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "error"
    state["result"]["error"] = state.get("error", "Unknown error")
    append_log(
        state,
        "supervisor",
        "pipeline_aborted",
        {
            "reason": state.get("error"),
        },
    )
    logger.error(f"Pipeline aborted: {state.get('error')}")
    return state


def review_terminal_node(state: AgentState) -> AgentState:
    """Terminal node for pending_review state without error."""
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "pending_review"
    append_log(
        state,
        "supervisor",
        "pipeline_paused",
        {
            "reason": "pending_review",
        },
    )
    return state


def audit_review_terminal_node(state: AgentState) -> AgentState:
    """Terminal node: audit gave up — awaits user confirmation to force-persist."""
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "pending_audit_review"
    state["result"]["giveup_record"] = state.get("giveup_record")
    state["result"]["audit_rejection_reason"] = state.get(
        "audit_rejection_reason"
    ) or state.get("audit_feedback")
    append_log(
        state,
        "supervisor",
        "pipeline_paused",
        {"reason": "pending_audit_review"},
    )
    return state


# ---------------------------------------------------------------------------
# Routing function for unified graph
# ---------------------------------------------------------------------------


def route_after_supervisor(state: AgentState) -> str:
    """
    Conditional edge: dispatch to the correct agent node after supervisor routing.
    Returns the node name that matches the routing_map in create_agent_graph().
    """
    agent = state.get("current_agent", "ingesta")
    # audit_review_terminal takes priority over error — it's the HITL path for
    # recoverable audit give-ups (state["error"] is kept for backward compat only).
    if agent == "audit_review_terminal":
        return "audit_review_terminal"
    if state.get("error"):
        return "error_terminal"
    routing_map = {
        "ingesta": "ingesta",
        "ingest": "ingesta",
        "import_existing": "import_existing",
        "contador": "contador",
        "tributario": "tributario",
        "auditor": "auditor",
        "db_persist": "db_persist",
        "persist": "db_persist",
        "reportero": "reportero",
        "review_terminal": "review_terminal",
        "audit_review_terminal": "audit_review_terminal",
    }
    return routing_map.get(agent, "error_terminal")
