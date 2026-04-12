"""
Reportero Chatbot API — conversational financial assistant.

Endpoints:
  POST /chat          Non-streaming (synchronous) chat
  POST /chat/stream   SSE streaming chat
  GET  /chat/sessions          List sessions
  GET  /chat/sessions/{id}/messages  Get session history
  DELETE /chat/sessions/{id}   Delete session
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool

from app.models.chat_schemas import ChatRequest, ChatResponse, SessionSummary
from app.services import chat_service
from app.services.nit_utils import normalize_nit

logger = logging.getLogger(__name__)

router = APIRouter()


def _normalize_request_nit(request: ChatRequest) -> ChatRequest:
    """Validate and normalize company_nit on the request."""
    if request.company_nit:
        try:
            request.company_nit = normalize_nit(request.company_nit)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")
    return request


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Non-streaming chat endpoint.

    Classifies the user's question, gathers relevant financial data,
    and returns a conversational response with optional structured data cards.
    """
    request = _normalize_request_nit(request)
    try:
        return chat_service.handle_chat_message(request)
    except Exception as exc:
        logger.error("Chat endpoint error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Error interno procesando la consulta"
        )


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """
    SSE streaming chat endpoint.

    Streams the LLM response token-by-token as SSE events:
      event: token    — individual text chunk
      event: data     — structured financial data cards + metadata
      event: done     — session_id confirmation

    Frontend should use EventSource or fetch with ReadableStream.
    """
    request = _normalize_request_nit(request)

    async def event_generator():
        try:
            async for event in iterate_in_threadpool(
                chat_service.handle_chat_stream(request)
            ):
                yield event
        except Exception as exc:
            logger.error("Chat stream error: %s", exc, exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": "Error interno del servidor"}),
            }

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionSummary])
async def get_sessions(
    company_nit: str | None = Query(None, description="Filter by company NIT"),
):
    """List chat sessions, optionally filtered by company NIT."""
    nit = None
    if company_nit:
        try:
            nit = normalize_nit(company_nit)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")
    return chat_service.list_sessions(nit)


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Get all messages for a chat session."""
    messages = chat_service.get_session_messages(session_id)
    if not messages:
        raise HTTPException(
            status_code=404, detail=f"Session {session_id} not found or empty"
        )
    return messages


@router.delete("/sessions/{session_id}")
async def remove_session(session_id: str):
    """Delete a chat session and all its messages."""
    deleted = chat_service.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"status": "deleted", "session_id": session_id}
