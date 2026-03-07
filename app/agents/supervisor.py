"""
Supervisor and validation nodes for ingest and process graphs.
"""

import logging
from pathlib import Path

from app.agents.state import AgentState
from app.core.database import SessionLocal
from app.services import db_service
from app.services.validation_engine import ValidationResult, get_validator

logger = logging.getLogger(__name__)


def supervisor_node(state: AgentState) -> AgentState:
    """Ingest supervisor: validates file input and routes to ingest worker."""
    file_path = state["file_path"]

    if not state.get("validation_history"):
        state["validation_history"] = []
    if not state.get("current_agent"):
        state["current_agent"] = ""
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if not state.get("correction_feedback"):
        state["correction_feedback"] = None

    if not Path(file_path).exists():
        state["error"] = f"File not found: {file_path}"
        logger.error(state["error"])
        return state

    if not file_path.lower().endswith(".pdf"):
        state["error"] = f"Only PDF files are supported. Got: {file_path}"
        logger.error(state["error"])
        return state

    state["mode"] = "ingest"
    state["current_agent"] = "ingesta"
    return state


def process_supervisor_node(state: AgentState) -> AgentState:
    """Process supervisor: validates staged input and routes to contador worker."""
    if not state.get("validation_history"):
        state["validation_history"] = []
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if state.get("correction_feedback") is None:
        state["correction_feedback"] = None

    raw_txs = state.get("raw_transactions", [])
    if not raw_txs:
        state["error"] = "Process supervisor: no staged transactions to process"
        return state

    state["mode"] = "process"
    state["current_agent"] = "contador"
    state["current_stage"] = "routing"
    return state


def validate_output_node(state: AgentState) -> AgentState:
    """Generic schema validation node (used by ingest graph)."""
    if state.get("error"):
        return state

    agent_name = state.get("current_agent", "ingesta")
    raw_output = state.get("interpreted_data", {})
    attempt = state.get("retry_count", 0) + 1

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
        state["correction_feedback"] = None
        state["retry_count"] = 0
        if result.validated_output:
            state["result"]["validated_data"] = result.validated_output.model_dump(mode="json")
        return state

    if validator.should_retry(result):
        state["correction_feedback"] = validator.build_correction_prompt(result)
        state["retry_count"] = attempt
        return state

    state["error"] = (
        f"Schema validation failed for '{agent_name}' after {attempt} attempts. "
        f"Last errors:\n{result.error_summary()}"
    )
    state["correction_feedback"] = None
    state["result"]["status"] = "validation_error"
    state["result"]["validation_errors"] = result.errors
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
    finally:
        db.close()


def validate_contador_output_node(state: AgentState) -> AgentState:
    """Validate contador output schema + PUC existence business rule."""
    if state.get("error"):
        return state

    agent_name = "contador"
    raw_output = state.get("contador_output") or state.get("interpreted_data", {})
    attempt = state.get("retry_count", 0) + 1

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
            return state

        state["error"] = (
            f"Schema validation failed for '{agent_name}' after {attempt} attempts. "
            f"Last errors:\n{result.error_summary()}"
        )
        state["result"]["status"] = "validation_error"
        state["result"]["validation_errors"] = result.errors
        state["correction_feedback"] = None
        return state

    validated = result.validated_output.model_dump(mode="json") if result.validated_output else raw_output
    missing = _missing_puc_codes(validated)

    if missing:
        missing_msg = (
            "Los siguientes codigos PUC no existen o no estan activos en base de datos: "
            + ", ".join(missing)
            + ". Corrige los asientos usando codigos PUC validos."
        )
        if attempt < validator.MAX_RETRIES:
            state["correction_feedback"] = missing_msg
            state["retry_count"] = attempt
            return state

        state["error"] = (
            f"PUC validation failed for '{agent_name}' after {attempt} attempts. "
            f"Missing codes: {', '.join(missing)}"
        )
        state["result"]["status"] = "validation_error"
        state["result"]["validation_errors"] = [
            {
                "loc": ["asientos"],
                "msg": missing_msg,
                "type": "puc_not_found",
            }
        ]
        state["correction_feedback"] = None
        return state

    state["correction_feedback"] = None
    state["retry_count"] = 0
    state["contador_output"] = validated
    state["interpreted_data"] = validated
    state["current_stage"] = "validated"
    state["result"]["validated_data"] = validated
    return state


def should_retry_agent(state: AgentState) -> str:
    """Conditional edge for ingest graph retries."""
    if state.get("correction_feedback"):
        return "retry"
    return "end"


def should_retry_contador(state: AgentState) -> str:
    """Conditional edge for process graph retries."""
    if state.get("correction_feedback"):
        return "retry"
    return "end"
