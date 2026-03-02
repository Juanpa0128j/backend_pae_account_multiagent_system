"""
LangGraph StateGraph for the pilot agent.
Orchestrates the flow between Supervisor, Worker, and Validation nodes.

Graph structure:
  Supervisor → Ingesta → Validate Output ─┬─(valid)──→ END
                  ↑                        │
                  └───── (retry) ──────────┘
"""

import logging
from langgraph.graph import StateGraph, END
from app.agents.state import AgentState
from app.agents.supervisor import (
    supervisor_node,
    validate_output_node,
    should_retry_agent,
)
from app.agents.ingest_agent import ingest_node
from app.agents.persist_node import db_persist_node

logger = logging.getLogger(__name__)


def create_agent_graph() -> StateGraph:
    """
    Create and return the agent graph with validation & retry.

    Graph structure:
    Supervisor → Ingesta → ValidateOutput ─┬─ "end"   → db_persist → END
                    ↑                       └─ "retry" → Ingesta
    
    Returns:
        Compiled StateGraph ready for invocation
    """
    
    # Create the graph
    graph = StateGraph(AgentState)
    
    # Add nodes
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("ingesta", ingest_node)
    graph.add_node("validate_output", validate_output_node)
    graph.add_node("db_persist", db_persist_node)
    
    # Define edges
    graph.add_edge("supervisor", "ingesta")
    graph.add_edge("ingesta", "validate_output")
    
    # Conditional edge: retry agent or persist to DB
    graph.add_conditional_edges(
        "validate_output",
        should_retry_agent,
        {
            "retry": "ingesta",
            "end": "db_persist",
        },
    )
    
    # db_persist always goes to END
    graph.add_edge("db_persist", END)
    
    # Set entry point
    graph.set_entry_point("supervisor")
    
    # Compile the graph
    compiled_graph = graph.compile()
    
    logger.info("Agent graph created and compiled (with validation loop + DB persist)")
    return compiled_graph


def invoke_agent(file_path: str, initial_state: dict | None = None) -> dict:
    """
    Invoke the agent with a file path.
    
    Args:
        file_path: Path to the PDF file to process
        initial_state: Optional dictionary with additional state fields to
            pre-populate before invoking the graph (e.g.
            ``{"ingest_id": "abc123"}``).  Safe to use with supplemental
            fields such as ``ingest_id``; ``file_path`` is always taken from
            the explicit first argument even if ``initial_state`` includes it.
        
    Returns:
        Result dictionary with status, data, or error.
        Includes validated_data and validation_history when available.
    """
    
    graph = create_agent_graph()
    
    # Initialize state with defaults
    state: AgentState = {
        "file_path": file_path,
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": None,
        "db_result": None,
    }
    
    # Merge caller-supplied overrides (e.g. ingest_id).
    # file_path is always taken from the explicit argument.
    if initial_state:
        state.update(initial_state)
        state["file_path"] = file_path  # ensure explicit arg always wins
    
    # Invoke the graph
    logger.info(f"Invoking agent for file: {file_path}")
    final_state = graph.invoke(state)
    
    # Enrich result with validation info and DB result
    result = final_state["result"]
    result["validation_history"] = final_state.get("validation_history", [])
    result["db_result"] = final_state.get("db_result")
    
    return result
