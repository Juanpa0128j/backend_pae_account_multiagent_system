"""
Agent state definitions for the pilot agent.
Defines the shared state passed between agent nodes.
"""

from typing import List, Optional, TypedDict


class ValidationRecord(TypedDict):
    """Record of a single validation attempt."""

    agent_name: str
    attempt: int
    is_valid: bool
    errors: List[dict]
    timestamp: str


class LogEntry(TypedDict):
    """Structured execution log entry written by each agent node."""

    timestamp: str  # ISO UTC string
    agent: str  # node/agent name
    event: (
        str  # event type: routing_start | routing_complete | validation_success | etc.
    )
    details: dict  # free-form event-specific payload


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
    - tributario_output: Structured tributario output (TributarioOutput-compatible dict)
    - company_config: Tax rates loaded from company_settings DB for the nit_receptor; None = use defaults
    - process_id: Process job id when running accounting pipeline
    - pending_transaction_id: Staged transaction id currently being posted
    - current_stage: Human-readable pipeline stage for status updates
    - agent_log: Timeline entries for process status/debugging (List[LogEntry])
    - auditor_output: Structured auditor output (AuditorOutput-compatible dict)
    - audit_approved: Whether the auditor approved the transaction
    - audit_rejection_reason: Reason for rejection if not approved
    - audit_decision: Decision from Auditor agent: "approved" | "rejected" | None
    - audit_feedback: Rejection reason from Auditor, fed back to Contador for retry
    - report_type: Reporting pipeline — one of "balance" | "pnl" | "cashflow" | "iva" | "withholdings" | "analysis"
    - report_params: Reporting pipeline — filter params, e.g. {"start_date": "2026-01-01", "end_date": "2026-01-31", "include_analysis": true}
    - company_nit: Explicitly set company NIT from the API caller; overrides auto-detected entity_nit from document
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
    tributario_output: dict
    company_config: Optional[dict]
    process_id: Optional[str]
    pending_transaction_id: Optional[str]
    current_stage: Optional[str]
    agent_log: List[LogEntry]
    auditor_output: dict
    audit_approved: Optional[bool]
    audit_rejection_reason: Optional[str]
    audit_decision: Optional[str]
    audit_feedback: Optional[str]
    audit_rejection_count: (
        int  # how many times auditor has rejected — not reset by retry_count
    )
    report_type: Optional[str]
    report_params: Optional[dict]
    # Document classification fields (ingestion pipeline)
    document_classification: Optional[dict]  # DocumentClassification serialized
    pathway: Optional[str]  # "build_from_scratch" | "work_with_existing"
    parsed_content: Optional[list]  # Structured tabular data from Excel sheets
    company_nit: Optional[
        str
    ]  # Explicitly set by API caller; overrides auto-detected entity_nit
    source_document: dict  # Full structured extraction dict from ingest pipeline (raw_data from TransactionPending)
    # Phase 2 — audit finding buckets (append-only during pipeline run)
    pipeline_warnings: List[dict]  # WARNING/INFO AuditFinding dicts
    unfixable_findings: List[dict]  # BLOCKER AuditFinding dicts with fixable=False
    audit_reports: List[dict]  # AuditReport dicts (populated in Phase 3+)
    retry_budget: dict  # per-target remaining retries, e.g. {"contador": 2}
    giveup_record: Optional[dict]  # GiveUpRecord dict when loop gives up (Phase 4+)
    force_persist: bool  # If True, skip audit blocker checks and force DB persist
