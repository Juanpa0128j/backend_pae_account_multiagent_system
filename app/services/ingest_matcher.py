"""Ingest matcher service — suggests potential merges for multi-page documents."""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.database import IngestJob, IngestStatus

_ALLOWED_STATUSES = (
    IngestStatus.PENDING_PROCESSING,
    IngestStatus.COMPLETED,
    IngestStatus.PENDING_REVIEW,
)


def find_merge_candidates(
    db: Session,
    company_nit: str,
    *,
    time_window_minutes: int = 5,
    limit: int = 50,
) -> list[dict]:
    """Find ingest jobs that look like pages of the same document.

    Matching criteria:
    - Same company_nit
    - Status in (PENDING_PROCESSING, COMPLETED, PENDING_REVIEW)
    - Not already marked as merged/cancelled
    - Within time_window_minutes of each other
    - Same document_type (or both null)

    Returns groups of ingest_ids that are potential merge candidates.
    Example: [{"ingest_ids": ["id1", "id2"], "reason": "Same company, same type, 2 min apart"}]
    """
    jobs = (
        db.query(IngestJob)
        .filter(IngestJob.company_nit == company_nit)
        .filter(IngestJob.status.in_(_ALLOWED_STATUSES))
        .order_by(IngestJob.created_at.asc())
        .limit(limit)
        .all()
    )

    if len(jobs) < 2:
        return []

    groups: list[list[IngestJob]] = []
    current_group = [jobs[0]]

    for job in jobs[1:]:
        last = current_group[-1]
        same_type = last.document_type == job.document_type
        within_window = False
        if last.created_at and job.created_at:
            diff = job.created_at - last.created_at
            within_window = diff <= timedelta(minutes=time_window_minutes)

        if same_type and within_window:
            current_group.append(job)
        else:
            if len(current_group) >= 2:
                groups.append(current_group)
            current_group = [job]

    if len(current_group) >= 2:
        groups.append(current_group)

    result: list[dict] = []
    for group in groups:
        ingest_ids = [j.id for j in group]
        doc_type = group[0].document_type
        first = group[0].created_at
        last = group[-1].created_at
        seconds_diff = int((last - first).total_seconds()) if first and last else 0
        reason = f"Same company_nit, same document_type, {seconds_diff} seconds apart"
        created_at_range = ""
        if first and last:
            first_str = first.isoformat().replace("+00:00", "Z")
            last_str = last.isoformat().replace("+00:00", "Z")
            created_at_range = f"{first_str}/{last_str}"
        result.append(
            {
                "ingest_ids": ingest_ids,
                "document_type": doc_type,
                "company_nit": company_nit,
                "reason": reason,
                "created_at_range": created_at_range,
            }
        )

    return result
