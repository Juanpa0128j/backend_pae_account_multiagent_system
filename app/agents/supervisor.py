"""
Supervisor and validation nodes for ingest and process graphs.

The Supervisor is a finite state machine (FSM) that routes between all
architecture agents based on state['mode'] and state['current_agent']:

  mode == "ingest"   → validate file → ingesta
  mode == "process"  → contador → tributario → auditor → db_persist
  mode == "reporting"→ reportero

All routing decisions and validation outcomes are recorded in agent_log.
"""

import logging
from pathlib import Path

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.database import SessionLocal

from app.core.logger import get_logger
from app.services import db_service
from app.services.nit_utils import normalize_optional_nit
from app.services.validation_engine import ValidationResult, get_validator


logger = get_logger("app.agents.supervisor")


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

    append_log(state, "supervisor", "routing_start", {
        "mode": mode,
        "current_agent": current,
    })

    # ------------------------------------------------------------------
    # Ingest pipeline: file upload → ingesta → validate → db_persist
    # ------------------------------------------------------------------
    if mode in ("ingest", "") and not current:
        file_path = state.get("file_path", "")

        if not Path(file_path).exists():
            state["error"] = f"File not found: {file_path}"
            logger.error(state["error"])
            append_log(state, "supervisor", "routing_error", {
                "reason": "file_not_found", "file_path": file_path,
            })
            return state

        _SUPPORTED_EXTENSIONS = (".pdf", ".xlsx", ".xml", ".jpg", ".jpeg", ".png")
        if not any(file_path.lower().endswith(ext) for ext in _SUPPORTED_EXTENSIONS):
            state["error"] = (
                f"Unsupported file type. Accepted: PDF, Excel, XML, JPG, PNG. Got: {file_path}"
            )
            logger.error(state["error"])
            append_log(state, "supervisor", "routing_error", {
                "reason": "unsupported_format", "file_path": file_path,
            })
            return state

        # --- Extract text preview and classify document ---
        ext = Path(file_path).suffix.lower()
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
        except Exception as preview_err:
            logger.warning(
                "Supervisor: text preview extraction failed: %s", preview_err
            )

        # Classify document by content using LLM
        try:
            from app.services.doc_classifier import classify_document
            from app.models.document_types import IngestPathway

            classification = classify_document(
                text_preview=text_preview,
                source_format=ext.lstrip("."),
            )
            classification_dict = classification.model_dump(mode="json")
            classification_dict["entity_nit"] = normalize_optional_nit(
                classification_dict.get("entity_nit")
            )
            # If the caller explicitly provided a company_nit, use it instead of
            # the NIT auto-detected from the document content.
            if state.get("company_nit"):
                override_nit = normalize_optional_nit(state.get("company_nit"))
                if not override_nit:
                    state["error"] = "Supervisor: provided company_nit is empty after normalization"
                    append_log(state, "supervisor", "routing_error", {
                        "reason": "invalid_company_nit",
                    })
                    return state
                classification_dict["entity_nit"] = override_nit
                logger.info(
                    "Supervisor: company_nit override applied — using %s instead of auto-detected %s",
                    override_nit, classification.entity_nit,
                )
            state["document_classification"] = classification_dict
            state["pathway"] = classification.pathway.value

            append_log(state, "supervisor", "document_classified", {
                "doc_type": classification.doc_type.value,
                "pathway": classification.pathway.value,
                "confidence": classification.confidence,
            })

            if classification.pathway == IngestPathway.WORK_WITH_EXISTING:
                state["mode"] = "ingest"
                state["current_agent"] = "import_existing"
                logger.info(
                    "Supervisor: Vía B — routing to import_existing for %s (%s)",
                    file_path, classification.doc_type.value,
                )
                append_log(state, "supervisor", "routing_complete", {
                    "next_agent": "import_existing", "mode": "ingest",
                    "pathway": "work_with_existing",
                })
                return state
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
        append_log(state, "supervisor", "routing_complete", {
            "next_agent": "ingesta", "mode": "ingest",
        })
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
                append_log(state, "supervisor", "routing_error", {
                    "reason": "no_transactions",
                })
                return state
            state["current_agent"] = "contador"
            state["current_stage"] = "routing"
            append_log(state, "supervisor", "routing_complete", {
                "next_agent": "contador", "mode": "process",
            })
            return state

        if current == "contador":
            # Validate contador output before proceeding to tributario
            state = validate_contador_output_node(state)
            if state.get("correction_feedback"):
                # Validation failed — retry contador
                state["current_agent"] = "contador"
                append_log(state, "supervisor", "routing_complete", {
                    "next_agent": "contador", "reason": "validation_failed",
                })
            elif state.get("error"):
                # Validation exhausted or non-retriable error — terminal
                state["current_agent"] = ""
                append_log(state, "supervisor", "routing_error", {
                    "reason": "contador_validation_exhausted",
                })
            else:
                state["current_agent"] = "tributario"
                append_log(state, "supervisor", "routing_complete", {
                    "next_agent": "tributario",
                })
            return state

        if current == "tributario":
            # Validate TributarioOutput schema before advancing to auditor
            tributario_out = state.get("tributario_output", {})
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
                state["current_agent"] = "auditor"
                append_log(state, "supervisor", "routing_complete", {
                    "next_agent": "auditor",
                })
            else:
                logger.error(
                    f"Supervisor: tributario output INVALID — "
                    f"{result.error_summary()}"
                )
                state["error"] = (
                    f"Tributario output schema validation failed: "
                    f"{result.error_summary()}"
                )
                state["current_agent"] = ""
                append_log(state, "supervisor", "routing_error", {
                    "reason": "tributario_validation_failed",
                    "errors": result.errors[:3],
                })
            return state

        if current == "auditor":
            # Validate AuditorOutput schema before deciding whether to persist.
            state = validate_auditor_output_node(state)
            if state.get("correction_feedback"):
                state["current_agent"] = "auditor"
                append_log(state, "supervisor", "routing_complete", {
                    "next_agent": "auditor", "reason": "validation_failed",
                })
            elif state.get("error"):
                state["current_agent"] = ""
                append_log(state, "supervisor", "routing_error", {
                    "reason": "auditor_validation_exhausted",
                })
            elif state.get("audit_approved") is False:
                rejection_count = state.get("audit_rejection_count", 0) + 1
                state["audit_rejection_count"] = rejection_count
                if rejection_count > 2:
                    # Max audit retries exceeded — persist with rejection flags.
                    state["current_agent"] = "db_persist"
                    logger.warning(
                        "Supervisor: Audit rejection retry limit reached — persisting with rejection"
                    )
                    append_log(state, "supervisor", "routing_complete", {
                        "next_agent": "db_persist", "reason": "audit_rejected_max_retries",
                    })
                else:
                    state["current_agent"] = "contador"
                    state["correction_feedback"] = (
                        state.get("audit_rejection_reason")
                        or state.get("audit_feedback")
                        or "Audit rejected - please reclassify"
                    )
                    logger.warning(
                        "Supervisor: Auditor rejected — re-routing to Contador (rejection %d/2)",
                        rejection_count,
                    )
                    append_log(state, "supervisor", "routing_complete", {
                        "next_agent": "contador", "reason": "audit_rejected",
                    })
            else:
                state["current_agent"] = "db_persist"
                append_log(state, "supervisor", "routing_complete", {
                    "next_agent": "db_persist", "decision": "approved",
                })
            return state

    # ------------------------------------------------------------------
    # Reporting pipeline
    # ------------------------------------------------------------------
    if mode == "reporting":
        state["current_agent"] = "reportero"
        append_log(state, "supervisor", "routing_complete", {
            "next_agent": "reportero", "mode": "reporting",
        })
        return state

    # ------------------------------------------------------------------
    # Unknown state — fail gracefully
    # ------------------------------------------------------------------
    state["error"] = (
        f"Supervisor: unknown mode '{mode}' / current_agent '{current}'"
    )
    logger.error(state["error"])
    append_log(state, "supervisor", "routing_error", {
        "reason": "unknown_state", "mode": mode, "current_agent": current,
    })
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
# Validation nodes
# ---------------------------------------------------------------------------

def validate_output_node(state: AgentState) -> AgentState:
    """Generic schema validation node (used by ingest graph)."""
    if state.get("error"):
        return state

    agent_name = state.get("current_agent", "ingesta")
    raw_output = state.get("interpreted_data", {})
    attempt = state.get("retry_count", 0) + 1

    append_log(state, agent_name, "validation_start", {"attempt": attempt})

    validator = get_validator()
    result: ValidationResult = validator.validate(
        agent_name, raw_output, attempt=attempt
    )

    state["validation_history"].append(
        {
            "agent_name": agent_name,
            "attempt": attempt,
            "is_valid": result.is_valid,
            "errors": result.errors,
            "timestamp": result.timestamp,
        }
    )

    if result.is_valid:
        logger.info(f"Supervisor: output from '{agent_name}' VALID (attempt {attempt})")
        state["correction_feedback"] = None
        state["retry_count"] = 0
        if result.validated_output:
            state["result"]["validated_data"] = result.validated_output.model_dump(
                mode="json"
            )
        append_log(state, agent_name, "validation_success", {"attempt": attempt})
        return state

    if validator.should_retry(result):
        logger.warning(
            f"Supervisor: output from '{agent_name}' INVALID — "
            f"scheduling retry {attempt}/{validator.MAX_RETRIES}"
        )
        state["correction_feedback"] = validator.build_correction_prompt(result)
        state["retry_count"] = attempt
        append_log(state, agent_name, "validation_failure", {
            "attempt": attempt,
            "error_count": len(result.errors),
            "will_retry": True,
        })
        return state

    logger.error(
        f"Supervisor: output from '{agent_name}' failed after {attempt} attempts"
    )
    state["error"] = (
        f"Schema validation failed for '{agent_name}' after {attempt} attempts. "
        f"Last errors:\n{result.error_summary()}"
    )
    state["correction_feedback"] = None
    state["result"]["status"] = "validation_error"
    state["result"]["validation_errors"] = result.errors
    append_log(state, agent_name, "validation_exhausted", {
        "attempt": attempt,
        "errors": result.errors[:3],
    })
    return state


def _missing_puc_codes(contador_output: dict) -> list[str]:
    """Return missing PUC codes from DB for a contador output payload."""
    asientos = contador_output.get("asientos", [])
    codes = sorted(
        {
            str(a.get("cuenta_puc", "")).strip()
            for a in asientos
            if a.get("cuenta_puc")
        }
    )
    if not codes:
        return []

    db = SessionLocal()
    try:
        return [code for code in codes if not db_service.validate_puc_exists(db, code)]
    except Exception as e:
        msg = f"DB error during PUC validation: {e}"
        logger.error(msg)
        raise RuntimeError(msg) from e
    finally:
        db.close()


def validate_contador_output_node(state: AgentState) -> AgentState:
    """Validate contador output schema + PUC existence business rule."""
    if state.get("error"):
        return state

    agent_name = "contador"
    raw_output = state.get("contador_output") or state.get("interpreted_data", {})
    attempt = state.get("retry_count", 0) + 1

    append_log(state, agent_name, "validation_start", {"attempt": attempt})

    validator = get_validator()
    result: ValidationResult = validator.validate(
        agent_name, raw_output, attempt=attempt
    )

    state["validation_history"].append(
        {
            "agent_name": agent_name,
            "attempt": attempt,
            "is_valid": result.is_valid,
            "errors": result.errors,
            "timestamp": result.timestamp,
        }
    )

    if not result.is_valid:
        if validator.should_retry(result):
            state["correction_feedback"] = validator.build_correction_prompt(result)
            state["retry_count"] = attempt
            append_log(state, agent_name, "validation_failure", {
                "attempt": attempt,
                "error_count": len(result.errors),
                "will_retry": True,
            })
            return state

        state["error"] = (
            f"Schema validation failed for '{agent_name}' after {attempt} attempts. "
            f"Last errors:\n{result.error_summary()}"
        )
        state["result"]["status"] = "validation_error"
        state["result"]["validation_errors"] = result.errors
        state["correction_feedback"] = None
        append_log(state, agent_name, "validation_exhausted", {
            "attempt": attempt, "errors": result.errors[:3],
        })
        return state

    validated = (
        result.validated_output.model_dump(mode="json")
        if result.validated_output
        else raw_output
    )

    try:
        missing = _missing_puc_codes(validated)
    except Exception as e:
        msg = f"DB error during PUC validation: {e}"
        logger.error(msg)
        state["error"] = msg
        append_log(state, agent_name, "node_error", {"error": msg})
        return state

    if missing:
        missing_msg = (
            "Los siguientes codigos PUC no existen o no estan activos en base de datos: "
            + ", ".join(missing)
            + ". Corrige los asientos usando codigos PUC validos."
        )
        if attempt < validator.MAX_RETRIES:
            state["correction_feedback"] = missing_msg
            state["retry_count"] = attempt
            append_log(state, agent_name, "validation_failure", {
                "attempt": attempt,
                "reason": "missing_puc",
                "missing": missing,
            })
            return state

        state["error"] = (
            f"PUC validation failed for '{agent_name}' after {attempt} attempts. "
            f"Missing codes: {', '.join(missing)}"
        )
        state["result"]["status"] = "validation_error"
        state["result"]["validation_errors"] = [
            {"loc": ["asientos"], "msg": missing_msg, "type": "puc_not_found"}
        ]
        state["correction_feedback"] = None
        append_log(state, agent_name, "validation_exhausted", {
            "attempt": attempt, "reason": "puc_not_found", "missing": missing,
        })
        return state

    state["correction_feedback"] = None
    state["retry_count"] = 0
    state["contador_output"] = validated
    state["interpreted_data"] = validated
    state["current_stage"] = "validated"
    state["result"]["validated_data"] = validated
    append_log(state, agent_name, "validation_success", {"attempt": attempt})
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

MAX_CONTADOR_RETRIES = 3


def should_retry_contador(state: AgentState) -> str:
    """Conditional edge for contador retries in the process graph."""
    if state.get("correction_feedback") and state.get("retry_count", 0) < MAX_CONTADOR_RETRIES:
        return "retry"
    return "end"


def validate_auditor_output_node(state: AgentState) -> AgentState:
    """Validate AuditorOutput schema and propagate audit decision into state."""
    if state.get("error"):
        return state

    agent_name = "auditor"
    raw_output = state.get("auditor_output") or {}
    attempt = state.get("retry_count", 0) + 1

    validator = get_validator()
    result: ValidationResult = validator.validate(agent_name, raw_output, attempt=attempt)

    state["validation_history"].append(
        {
            "agent_name": agent_name,
            "attempt": attempt,
            "is_valid": result.is_valid,
            "errors": result.errors,
            "timestamp": result.timestamp,
        }
    )

    if not result.is_valid:
        if validator.should_retry(result):
            state["correction_feedback"] = validator.build_correction_prompt(result)
            state["retry_count"] = attempt
            return state
        state["error"] = (
            f"Schema validation failed for '{agent_name}' after {attempt} attempts. "
            f"Last errors:\n{result.error_summary()}"
        )
        if not state.get("result"):
            state["result"] = {}
        state["result"]["status"] = "validation_error"
        state["result"]["validation_errors"] = result.errors
        state["correction_feedback"] = None
        return state

    validated = (
        result.validated_output.model_dump(mode="json")
        if result.validated_output
        else raw_output
    )
    state["audit_approved"] = validated.get("aprobado", False)
    state["audit_rejection_reason"] = (
        validated.get("resumen") if not validated.get("aprobado") else None
    )
    state["audit_decision"] = "approved" if validated.get("aprobado") else "rejected"
    state["audit_feedback"] = (
        validated.get("resumen") if not validated.get("aprobado") else None
    )
    state["auditor_output"] = validated
    state["correction_feedback"] = None
    state["retry_count"] = 0
    state["current_stage"] = "audit_complete"
    if not state.get("result"):
        state["result"] = {}
    state["result"]["auditor_output"] = validated
    return state


MAX_AUDITOR_RETRIES = 3


def should_retry_auditor(state: AgentState) -> str:
    """Conditional edge for auditor retries in the process graph."""
    if state.get("correction_feedback") and state.get("retry_count", 0) < MAX_AUDITOR_RETRIES:
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
    append_log(state, "supervisor", "pipeline_aborted", {
        "reason": state.get("error"),
    })
    logger.error(f"Pipeline aborted: {state.get('error')}")
    return state


# ---------------------------------------------------------------------------
# Routing function for unified graph
# ---------------------------------------------------------------------------

def route_after_supervisor(state: AgentState) -> str:
    """
    Conditional edge: dispatch to the correct agent node after supervisor routing.
    Returns the node name that matches the routing_map in create_agent_graph().
    """
    if state.get("error"):
        return "error_terminal"
    agent = state.get("current_agent", "ingesta")
    routing_map = {
        "ingesta": "ingesta",
        "import_existing": "import_existing",
        "contador": "contador",
        "tributario": "tributario",
        "auditor": "auditor",
        "db_persist": "db_persist",
        "reportero": "reportero",
    }
    return routing_map.get(agent, "error_terminal")

