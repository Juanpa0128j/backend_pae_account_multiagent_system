"""
Pydantic schemas for the accountant-facing pipeline trace.

PipelineTrace is returned by GET /api/v1/process/{id}/trace and provides
a Spanish-language timeline of each agent step, any blockers found, and
the reason the system gave up if auto-fix was exhausted.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.audit import AuditFinding, GiveUpRecord


class TraceStep(BaseModel):
    """One agent execution step in the pipeline timeline."""

    model_config = ConfigDict(str_strip_whitespace=True)

    agent: str = Field(..., description="Internal agent name, e.g. contador")
    started_at: datetime
    ended_at: datetime
    status: Literal["ok", "warning", "retried", "failed", "skipped"]
    summary_es: str = Field(
        ..., description="One-line Spanish narrative for accountants"
    )
    details_es: List[str] = Field(
        default_factory=list, description="Bullet-list of findings or actions"
    )
    suggested_action_es: Optional[str] = Field(
        None, description="What the accountant should do for this step"
    )
    findings: List[AuditFinding] = Field(
        default_factory=list, description="Audit findings emitted during this step"
    )
    technical_ref: str = Field(
        default="", description="Log slice identifier for engineer drill-down"
    )

    @computed_field  # type: ignore[misc]
    @property
    def duration_ms(self) -> int:
        delta = self.ended_at - self.started_at
        return max(0, int(delta.total_seconds() * 1000))


class PipelineTrace(BaseModel):
    """Full accountant-facing trace for a completed or failed process run."""

    model_config = ConfigDict(str_strip_whitespace=True)

    process_id: str
    overall_status: Literal["completed", "completed_with_warnings", "failed"]
    steps: List[TraceStep] = Field(default_factory=list)
    blockers: List[AuditFinding] = Field(default_factory=list)
    give_up: Optional[GiveUpRecord] = None
