"""
Ingest pipeline router.

Validates file input and routes to the ingest worker (ingesta).
Extracted from supervisor_node in supervisor.py for separation of concerns.
"""

from decimal import Decimal
from pathlib import Path

from app.agents.agent_utils import append_log
from app.agents.ingest_agent import _build_llama_parse_kwargs
from app.agents.state import AgentState
from app.agents.validation_rules import validate_auditor_output_node  # noqa: F401
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.database import IngestStatus
from app.services import db_service
from app.services.doc_classifier import classify_document
from app.services.nit_utils import normalize_optional_nit

logger = get_logger("app.agents.routing.ingest_router")


def route_ingest(state: AgentState) -> AgentState:
    """
    Handle all ingest-mode routing.

    Validates file input, classifies the document, and routes to the
    appropriate ingest worker or terminal node.
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
        logger.warning("Supervisor: text preview extraction failed: %s", preview_err)

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
            provided_company_nit = normalize_optional_nit(state.get("company_nit"))
            provided_company_name: str | None = None
            if provided_company_nit:
                db = SessionLocal()
                try:
                    cs = db_service.get_company_settings(db, provided_company_nit)
                    if cs and cs.nombre:
                        provided_company_name = cs.nombre
                finally:
                    db.close()

            classification = classify_document(
                text_preview=text_preview,
                source_format=ext.lstrip("."),
                company_nit=provided_company_nit,
                company_name=provided_company_name,
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
                        DocumentType(resolved_doc_type) if resolved_doc_type else None
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
