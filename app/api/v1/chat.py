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

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.auth import CurrentUser, get_current_user
from app.core.limiter import limiter
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool

from app.core.database import get_db
from app.models.chat_schemas import ChatRequest, ChatResponse, SessionSummary
from app.services import chat_service, db_service
from app.services.nit_utils import normalize_nit
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


def _normalize_request_nit(request: ChatRequest) -> ChatRequest:
    """Validate and normalize company_nit on the request."""
    if request.company_nit:
        try:
            request.company_nit = normalize_nit(request.company_nit)
        except ValueError as e:
            raise HTTPException(
                status_code=422, detail=f"El NIT de la empresa no es válido: {e}"
            )
    return request


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat(
    request: Request,
    chat_request: ChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Non-streaming chat endpoint.

    Classifies the user's question, gathers relevant financial data,
    and returns a conversational response with optional structured data cards.
    """
    chat_request = _normalize_request_nit(chat_request)
    try:
        return chat_service.handle_chat_message(chat_request)
    except Exception as exc:
        logger.error("Chat endpoint error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Error interno procesando la consulta"
        )


@router.post("/stream")
@limiter.limit("30/minute")
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    SSE streaming chat endpoint.

    Streams the LLM response token-by-token as SSE events:
      event: token    — individual text chunk
      event: data     — structured financial data cards + metadata
      event: done     — session_id confirmation

    Frontend should use EventSource or fetch with ReadableStream.
    """
    chat_request_arg = _normalize_request_nit(chat_request)

    async def event_generator():
        try:
            async for event in iterate_in_threadpool(
                chat_service.handle_chat_stream(chat_request_arg)
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
@limiter.limit("60/minute")
async def get_sessions(
    request: Request,
    company_nit: str | None = Query(None, description="Filter by company NIT"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List chat sessions, optionally filtered by company NIT."""
    nit = None
    if company_nit:
        try:
            nit = normalize_nit(company_nit)
        except ValueError as e:
            raise HTTPException(
                status_code=422, detail=f"El NIT de la empresa no es válido: {e}"
            )
    return chat_service.list_sessions(nit)


@router.get("/sessions/{session_id}/messages")
@limiter.limit("60/minute")
async def get_session_messages(
    request: Request,
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all messages for a chat session."""
    messages = chat_service.get_session_messages(session_id)
    if not messages:
        raise HTTPException(
            status_code=404, detail=f"Sesión {session_id} no encontrada o vacía."
        )
    return messages


@router.delete("/sessions/{session_id}")
@limiter.limit("30/minute")
async def remove_session(
    request: Request,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Soft-delete a chat session."""
    found = db_service.soft_delete_chat_session(db, session_id)
    if not found:
        raise HTTPException(
            status_code=404, detail=f"Sesión {session_id} no encontrada."
        )
    return {"status": "deleted", "session_id": session_id}


@router.post("/sessions/{session_id}/restore")
@limiter.limit("30/minute")
async def restore_session(
    request: Request,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Restore a soft-deleted chat session."""
    row = db_service.restore_chat_session(db, session_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Sesión no encontrada o ya activa.",
        )
    return {"status": "restored", "session_id": session_id}
