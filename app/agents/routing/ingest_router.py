"""Ingest pipeline router.

Handles file validation, preview extraction, document classification,
pending-review gate, and Vía A/B pathway resolution.
"""

from pathlib import Path

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.document_types import DocumentType, IngestPathway
from app.services import db_service
from app.services.classification_persistence import (
    load_confirmed_classification,
    mark_pending_review,
    save_classification_metadata,
)
from app.services.file_preview import extract_preview
from app.services.nit_utils import normalize_optional_nit

logger = get_logger("app.agents.routing.ingest_router")

_SUPPORTED_EXTENSIONS = (".pdf", ".xlsx", ".xml", ".jpg", ".jpeg", ".png")


def route(state: AgentState) -> AgentState:
    """Route ingest pipeline: validate → preview → classify → pathway → target."""
    file_path = state.get("file_path", "")

    # File validation
    if not Path(file_path).exists():
        state["error"] = f"File not found: {file_path}"
        logger.error(state["error"])
        append_log(
            state,
            "supervisor",
            "routing_error",
            {"reason": "file_not_found", "file_path": file_path},
        )
        return state

    if not any(file_path.lower().endswith(ext) for ext in _SUPPORTED_EXTENSIONS):
        state["error"] = (
            f"Unsupported file type. Accepted: PDF, Excel, XML, JPG, PNG. Got: {file_path}"
        )
        logger.error(state["error"])
        append_log(
            state,
            "supervisor",
            "routing_error",
            {"reason": "unsupported_format", "file_path": file_path},
        )
        return state

    # Extract preview
    text_preview, parsed_content = extract_preview(file_path)
    if parsed_content is not None:
        state["parsed_content"] = parsed_content
    state["raw_text"] = text_preview

    ext = Path(file_path).suffix.lower()
    file_name_lower = Path(file_path).name.lower()

    # Classification
    classification = None
    classification_dict = None
    ingest_id = str(state.get("ingest_id") or "").strip()
    ingest_job = None

    if ingest_id:
        db = SessionLocal()
        try:
            ingest_job = db_service.get_ingest_job(db, ingest_id)
        finally:
            db.close()

    classification_dict = load_confirmed_classification(ingest_job)
    use_confirmed = classification_dict is not None

    if not use_confirmed:
        from app.services.doc_classifier import classify_document

        classification = classify_document(
            text_preview=text_preview,
            source_format=ext.lstrip("."),
        )
        classification_error = getattr(classification, "error", None)
        if isinstance(classification_error, str) and classification_error:
            state["error"] = (
                "No fue posible clasificar el documento automáticamente. "
                "Reintente en unos minutos o seleccione el tipo manualmente."
            )
            append_log(
                state,
                "supervisor",
                "routing_error",
                {"reason": "classification_failed", "technical": classification.error},
            )
            return state

        classification_dict = classification.model_dump(mode="json")
        classification_dict["entity_nit"] = normalize_optional_nit(
            classification_dict.get("entity_nit")
        )

        # company_nit override
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
                    {"reason": "invalid_company_nit"},
                )
                return state
            classification_dict["entity_nit"] = override_nit

        state["document_classification"] = classification_dict
        state["pathway"] = classification.pathway.value

        # Persist classification metadata
        if ingest_id:
            db = SessionLocal()
            try:
                save_classification_metadata(db, ingest_id, classification)
            except Exception as persist_meta_err:
                logger.warning(
                    "ingest_router: failed to persist classification metadata: %s",
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

    # Resolve pathway and doc_type
    resolved_pathway = state.get("pathway")
    resolved_doc_type = (
        classification_dict.get("doc_type") if classification_dict else None
    )

    # Pending review gate
    if ingest_job and not use_confirmed:
        db = SessionLocal()
        try:
            confidence = classification.confidence if classification else None
            mark_pending_review(
                db, ingest_id, resolved_doc_type, resolved_pathway, confidence
            )
        except Exception as persist_review_err:
            logger.warning(
                "ingest_router: failed to mark pending_review: %s", persist_review_err
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

    # Pathway resolution
    if resolved_pathway == IngestPathway.WORK_WITH_EXISTING.value:
        if (
            ext == ".xlsx"
            and "extracto" in file_name_lower
            and "iva" not in file_name_lower
        ):
            logger.warning(
                "ingest_router: forcing build_from_scratch for potential bank statement file %s",
                file_path,
            )
            state["pathway"] = IngestPathway.BUILD_FROM_SCRATCH.value
            if classification_dict is not None:
                classification_dict["doc_type"] = DocumentType.EXTRACTO_BANCARIO.value
                state["document_classification"] = classification_dict
        else:
            via_b_doc_types = {
                DocumentType.BALANCE_GENERAL,
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
                state["current_agent"] = "ingesta"
                logger.info(
                    "ingest_router: Vía B — routing to ingesta for %s (%s)",
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
                "ingest_router: classifier returned work_with_existing for source doc_type=%s; forcing build_from_scratch",
                resolved_doc_type,
            )
            state["pathway"] = IngestPathway.BUILD_FROM_SCRATCH.value

    # Vía A (default)
    state["mode"] = "ingest"
    state["current_agent"] = "ingesta"
    logger.info("ingest_router: routing to ingesta for %s", file_path)
    append_log(
        state,
        "supervisor",
        "routing_complete",
        {"next_agent": "ingesta", "mode": "ingest"},
    )
    return state
