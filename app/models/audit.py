"""
Pydantic schemas for pipeline audit findings, reports, and give-up records.

These models flow through AgentState and are persisted as structured log entries
so the accountant-facing trace service can derive a human-readable timeline.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKER = "blocker"


class AuditTarget(str, Enum):
    INGEST = "ingest"
    CONTADOR = "contador"
    TRIBUTARIO = "tributario"
    PRE_PERSIST = "pre_persist"


ResponsibleAgent = Literal["ingest", "contador", "tributario", "persist"]


class AuditFinding(BaseModel):
    """Single audit finding emitted by any auditor node."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target: AuditTarget = Field(
        ..., description="Pipeline stage where finding was detected"
    )
    rule_id: str = Field(
        ...,
        description="Machine-readable rule identifier, e.g. TRIB-RETENCION-MISMATCH",
    )
    severity: Severity
    fixable: bool = Field(
        ...,
        description="If True the self-improvement loop will retry the responsible agent",
    )
    responsible_agent: ResponsibleAgent = Field(
        ..., description="Agent that should be retried or notified"
    )
    technical_message: str = Field(
        ..., description="Full technical description for engineers / LangSmith"
    )
    user_message_es: str = Field(
        ..., description="Plain Spanish explanation for accountants"
    )
    suggested_action_es: Optional[str] = Field(
        None, description="What the accountant should do next"
    )
    evidence: Dict[str, Any] = Field(
        default_factory=dict, description="Supporting data: ids, amounts, line refs"
    )


class AuditReport(BaseModel):
    """Result of a single auditor pass on one pipeline stage."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target: AuditTarget
    approved: bool
    findings: List[AuditFinding] = Field(default_factory=list)
    attempt: int = Field(..., ge=1)
    duration_ms: float = Field(..., ge=0)


class GiveUpRecord(BaseModel):
    """Recorded when the self-improvement loop exhausts retries for an agent."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target: ResponsibleAgent
    attempts: int = Field(..., ge=1)
    last_findings: List[AuditFinding] = Field(default_factory=list)
    explanation_es: str = Field(
        ..., description="Plain Spanish explanation of why auto-fix failed"
    )
