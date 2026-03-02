"""
Agent state definitions for the pilot agent.
Defines the shared state passed between agent nodes.
"""

from typing import Optional, TypedDict, Any, List


class ValidationRecord(TypedDict):
    """Record of a single validation attempt."""
    agent_name: str
    attempt: int
    is_valid: bool
    errors: List[dict]
    timestamp: str


class AgentState(TypedDict):
    """
    State object passed through the agent graph.

    Fields:
    - file_path: Path to the PDF file to be processed
    - raw_text: Raw text extracted from the PDF
    - interpreted_data: Structured data extracted by Gemini
    - result: Final JSON result to return to API
    - error: Error message if any step fails
    - validation_history: List of validation attempts across all agents
    - current_agent: Name of the agent currently being executed
    - correction_feedback: Feedback from validator to re-route to agent
    - retry_count: Current retry attempt for the active agent
    - ingest_id: Database ingest job ID (set by db_persist node)
    - db_result: Database persistence result summary
    """
    file_path: str
    raw_text: str
    interpreted_data: dict
    result: dict
    error: Optional[str]
    validation_history: List[ValidationRecord]
    current_agent: str
    correction_feedback: Optional[str]
    retry_count: int
    ingest_id: Optional[str]
    db_result: Optional[dict]
