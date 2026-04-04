"""Pydantic schemas for the Reportero Chatbot feature."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------

class ChatMessageSchema(BaseModel):
    """A single message in the conversation (for API display)."""
    role: Literal["user", "assistant"]
    content: str
    data_cards: list[FinancialDataCard] | None = None
    intent: str | None = None
    sources: list[str] | None = None
    created_at: str | None = None


class ChatRequest(BaseModel):
    """POST body for the chat endpoints."""
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None          # None → create a new session
    company_nit: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class FinancialDataCard(BaseModel):
    """Structured financial data attached to an assistant message."""
    card_type: str          # balance, pnl, cashflow, iva, withholdings, ratios, top_accounts, dashboard, analysis
    title: str
    data: dict[str, Any]


class ChatResponse(BaseModel):
    """Non-streaming (synchronous) chat response."""
    reply: str
    session_id: str
    data_cards: list[FinancialDataCard] = Field(default_factory=list)
    intent_detected: str
    sources: list[str] = Field(default_factory=list)


class SessionSummary(BaseModel):
    """Summary of a chat session for the session list."""
    id: str
    title: str | None = None
    company_nit: str | None = None
    message_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
