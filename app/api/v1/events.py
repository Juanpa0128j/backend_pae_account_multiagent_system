import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.services import db_service

router = APIRouter(prefix="/process", tags=["events"])
logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "pending_audit_review"}
)


@router.get("/{process_id}/events")
async def stream_process_events(
    process_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    process_job = db_service.get_process_job(db, process_id=process_id)
    if not process_job:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")

    async def event_generator():
        settings = get_settings()
        if not settings.hatchet_enabled:
            with SessionLocal() as fresh_db:
                job = db_service.get_process_job(fresh_db, process_id=process_id)
                if job:
                    yield f"data: {json.dumps({'status': job.status, 'progress': job.progress})}\n\n"
            return

        # Flag on: poll DB at 1s intervals, emit on status change
        last_status = None
        for _ in range(300):  # max 5 min
            await asyncio.sleep(1)
            with SessionLocal() as fresh_db:
                job = db_service.get_process_job(fresh_db, process_id=process_id)
                if not job:
                    break
                if job.status != last_status:
                    last_status = job.status
                    yield f"data: {json.dumps({'status': job.status, 'progress': job.progress})}\n\n"
                if job.status in TERMINAL_STATUSES:
                    break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
