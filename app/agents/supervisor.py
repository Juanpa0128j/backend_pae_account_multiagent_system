"""
Supervisor node for the agent graph.
Routes the input to appropriate worker nodes and validates all agent outputs.

The Supervisor enforces schema compliance: every agent output is validated
against its Pydantic schema.  Non-compliant outputs are rejected and
re-sent with correction feedback (up to MAX_RETRIES).
"""

import logging
from pathlib import Path
from typing import Any

from app.agents.state import AgentState
from app.core.database import SessionLocal
from app.services import db_service
from app.services.validation_engine import get_validator, ValidationResult

logger = logging.getLogger(__name__)


def supervisor_node(state: AgentState) -> AgentState:
    """
    Supervisor node: validates input and routes to Ingesta worker.
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state for next node
    """
    file_path = state["file_path"]
    
    # Initialise validation tracking fields if missing
    if not state.get("validation_history"):
        state["validation_history"] = []
    if not state.get("current_agent"):
        state["current_agent"] = ""
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if not state.get("correction_feedback"):
        state["correction_feedback"] = None
    
    # Validate file exists
    if not Path(file_path).exists():
        state["error"] = f"File not found: {file_path}"
        logger.error(state["error"])
        return state
    
    # Validate it's a PDF
    if not file_path.lower().endswith(".pdf"):
        state["error"] = f"Only PDF files are supported. Got: {file_path}"
        logger.error(state["error"])
        return state
    
    logger.info(f"Supervisor: Processing file {file_path}")
    state["current_agent"] = "ingesta"
    
    # Route to Ingesta (implicit - next node in graph)
    return state


def validate_output_node(state: AgentState) -> AgentState:
    """
    Post-agent validation node.
    
    Validates the interpreted_data produced by the current agent against
    its registered Pydantic schema.  If invalid and retries remain,
    sets correction_feedback so the graph can re-route to the agent.
    
    Args:
        state: Current agent state (must contain interpreted_data)
        
    Returns:
        Updated state — either with validated result or correction_feedback
    """
    # Skip if an upstream error already exists
    if state.get("error"):
        return state

    agent_name = state.get("current_agent", "ingesta")
    raw_output = state.get("interpreted_data", {})
    attempt = state.get("retry_count", 0) + 1

    validator = get_validator()
    result: ValidationResult = validator.validate(
        agent_name, raw_output, attempt=attempt
    )

    # Record in state history
    state["validation_history"].append({
        "agent_name": agent_name,
        "attempt": attempt,
        "is_valid": result.is_valid,
        "errors": result.errors,
        "timestamp": result.timestamp,
    })

    if result.is_valid:
        logger.info(
            f"Supervisor: Output from '{agent_name}' is VALID (attempt {attempt})"
        )
        state["correction_feedback"] = None
        state["retry_count"] = 0
        # Enrich the result dict with validated data
        if result.validated_output:
            state["result"]["validated_data"] = result.validated_output.model_dump(
                mode="json"
            )
        return state

    # --- Output is NOT valid ---
    if validator.should_retry(result):
        logger.warning(
            f"Supervisor: Output from '{agent_name}' INVALID — "
            f"scheduling retry {attempt}/{validator.MAX_RETRIES}"
        )
        state["correction_feedback"] = validator.build_correction_prompt(result)
        state["retry_count"] = attempt
        # Do NOT set error — the graph will re-route to the agent
        return state

    # Exhausted retries → hard failure
    logger.error(
        f"Supervisor: Output from '{agent_name}' failed validation "
        f"after {attempt} attempts. Marking as error."
    )
    state["error"] = (
        f"Schema validation failed for '{agent_name}' after "
        f"{attempt} attempts. Last errors:\n{result.error_summary()}"
    )
    state["correction_feedback"] = None
    state["result"]["status"] = "validation_error"
    state["result"]["validation_errors"] = result.errors
    return state


def should_retry_agent(state: AgentState) -> str:
    """
    Conditional edge function: decides whether to retry the agent
    or proceed to END.
    
    Returns:
        "retry"  → re-execute the current agent with correction feedback
        "end"    → proceed to END (either success or hard failure)
    """
    if state.get("correction_feedback"):
        return "retry"
    return "end"


# ---------------------------------------------------------------------------
# Process pipeline nodes and routing
# ---------------------------------------------------------------------------

def process_supervisor_node(state: AgentState) -> AgentState:
    """
    Process supervisor: validates staged input and routes to contador worker.
    """
    if not state.get("validation_history"):
        state["validation_history"] = []
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if state.get("correction_feedback") is None:
        state["correction_feedback"] = None

    raw_txs = state.get("raw_transactions", [])
    if not raw_txs:
        state["error"] = "process_supervisor: no staged transactions to process"
        return state

    state["mode"] = "process"
    state["current_agent"] = "contador"
    state["current_stage"] = "routing"
    return state


def _missing_puc_codes(contador_output: dict) -> list[str]:
    """Return PUC codes from a ContadorOutput that are absent from the DB."""
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
    """Validate ContadorOutput schema + PUC existence business rule."""
    if state.get("error"):
        return state

    agent_name = "contador"
    raw_output = state.get("contador_output") or state.get("interpreted_data", {})
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
        if not state.get("result"):
            state["result"] = {}
        state["result"]["status"] = "validation_error"
        state["result"]["validation_errors"] = [
            {"loc": ["asientos"], "msg": missing_msg, "type": "puc_not_found"}
        ]
        state["correction_feedback"] = None
        return state

    state["correction_feedback"] = None
    state["retry_count"] = 0
    state["contador_output"] = validated
    state["interpreted_data"] = validated
    state["current_stage"] = "validated"
    if not state.get("result"):
        state["result"] = {}
    state["result"]["validated_data"] = validated
    return state


def should_retry_contador(state: AgentState) -> str:
    """Conditional edge for contador retries in the process graph."""
    if state.get("correction_feedback"):
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
    state["auditor_output"] = validated
    state["correction_feedback"] = None
    state["retry_count"] = 0
    state["current_stage"] = "audit_complete"
    if not state.get("result"):
        state["result"] = {}
    state["result"]["auditor_output"] = validated
    return state


def should_retry_auditor(state: AgentState) -> str:
    """Conditional edge for auditor retries in the process graph."""
    if state.get("correction_feedback"):
        return "retry"
    return "end"
