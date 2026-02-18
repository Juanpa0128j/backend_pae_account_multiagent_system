"""
Supervisor node for the agent graph.
Routes the input to appropriate worker nodes.
"""

import logging
from pathlib import Path
from app.agents.state import AgentState

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
    
    # Route to Ingesta (implicit - next node in graph)
    return state
