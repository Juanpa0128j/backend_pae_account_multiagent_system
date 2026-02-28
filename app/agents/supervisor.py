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
