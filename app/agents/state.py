"""
Agent state definitions for the pilot agent.
Defines the shared state passed between agent nodes.
"""

from typing import Optional, TypedDict, List


class ValidationRecord(TypedDict):
    """Record of a single validation attempt."""
    agent_name: str
    attempt: int
    is_valid: bool
    errors: List[dict]
    timestamp: str


class LogEntry(TypedDict):
    """Structured execution log entry written by each agent node."""
    timestamp: str   # ISO UTC string
    agent: str       # node/agent name
    event: str       # event type: routing_start | routing_complete | validation_success | etc.
    details: dict    # free-form event-specific payload


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
    - mode: Pipeline mode: ingest or process
    - raw_transactions: Staged transactions used by contador (process pipeline)
    - contador_output: Structured contador output (ContadorOutput-compatible dict)
    - process_id: Process job id when running accounting pipeline
    - pending_transaction_id: Staged transaction id currently being posted
    - current_stage: Human-readable pipeline stage for status updates
    - agent_log: Timeline entries for process status/debugging (List[LogEntry])
    - audit_decision: Decision from Auditor agent: "approved" | "rejected" | None
    - audit_feedback: Rejection reason from Auditor, fed back to Contador for retry
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
    mode: str
    raw_transactions: List[dict]
    contador_output: dict
    process_id: Optional[str]
    pending_transaction_id: Optional[str]
    current_stage: Optional[str]
    agent_log: List[LogEntry]
    audit_decision: Optional[str]
    audit_feedback: Optional[str]
