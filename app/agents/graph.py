"""
LangGraph StateGraph for the pilot agent.
Orchestrates the flow between Supervisor and Ingesta nodes.
"""

import logging
from langgraph.graph import StateGraph, END
from app.agents.state import AgentState
from app.agents.supervisor import supervisor_node
from app.agents.ingest_agent import ingest_node

logger = logging.getLogger(__name__)


def create_agent_graph() -> StateGraph:
    """
    Create and return the agent graph.
    
    Graph structure:
    Supervisor → Ingesta → END
    
    Returns:
        Compiled StateGraph ready for invocation
    """
    
    # Create the graph
    graph = StateGraph(AgentState)
    
    # Add nodes
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("ingesta", ingest_node)
    
    # Define edges
    # After supervisor validates, go to ingesta
    graph.add_edge("supervisor", "ingesta")
    
    # After ingesta processes, end
    graph.add_edge("ingesta", END)
    
    # Set entry point
    graph.set_entry_point("supervisor")
    
    # Compile the graph
    compiled_graph = graph.compile()
    
    logger.info("Agent graph created and compiled")
    return compiled_graph


def invoke_agent(file_path: str) -> dict:
    """
    Invoke the agent with a file path.
    
    Args:
        file_path: Path to the PDF file to process
        
    Returns:
        Result dictionary with status, data, or error
    """
    
    graph = create_agent_graph()
    
    # Initialize state
    initial_state: AgentState = {
        "file_path": file_path,
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
    }
    
    # Invoke the graph
    logger.info(f"Invoking agent for file: {file_path}")
    final_state = graph.invoke(initial_state)
    
    # Return the result
    return final_state["result"]
