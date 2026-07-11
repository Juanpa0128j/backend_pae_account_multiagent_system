"""Shared storage for ingest upload bytes.

Uploaded files must survive container restarts and multi-instance routing
between upload and the PENDING_REVIEW classification-confirm resume, so the
bytes live in the ingest_files table (the DB is the only storage shared by
all instances). Local disk is parser scratch space only: parsers (LlamaParse
SDK, pypdf, openpyxl) consume filesystem paths, so ensure_local_files()
rehydrates blobs to deterministic scratch paths on whichever instance runs
the pipeline.
"""

import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.models.database import IngestFile, IngestJob

logger = get_logger(__name__)

FILE_TTL_DAYS = 7


class IngestFilesUnavailableError(Exception):
    """No blob, no scratch file, no legacy path — the upload must be redone."""

    detail = "El archivo ya no está disponible; vuelva a subirlo."

    def __init__(self):
        super().__init__(self.detail)


def _scratch_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "pae_uploads"
    d.mkdir(exist_ok=True)
    return d


def scratch_path(ingest_id: str, file_name: str) -> str:
    """Deterministic per-job scratch path (prefix avoids filename collisions)."""
    return str(_scratch_dir() / f"{ingest_id}_{Path(file_name).name}")


def store_files(db: Session, ingest_id: str, files: list[tuple[str, bytes]]) -> None:
    """Stage one blob row per (file_name, content). Caller commits."""
    for file_name, content in files:
        db.add(
            IngestFile(
                id=f"ingf_{uuid.uuid4().hex[:12]}",
                ingest_id=ingest_id,
                file_name=Path(file_name).name,
                content=content,
                created_at=datetime.now(timezone.utc),
            )
        )


def ensure_local_files(db: Session, job: IngestJob) -> list[str]:
    """Return local paths for every file of the job, rehydrating from DB blobs.

    Order of preference per file: existing scratch file → DB blob → legacy
    job.file_path (pre-migration jobs). Raises IngestFilesUnavailableError
    when a file is recoverable from none of the three.
    """
    expected = job.file_names if job.file_names else [job.file_name]
    blobs = {
        row.file_name: row
        for row in db.query(IngestFile).filter_by(ingest_id=str(job.id)).all()
    }

    paths: list[str] = []
    for name in expected:
        target = Path(scratch_path(str(job.id), name))
        if target.exists():
            paths.append(str(target))
            continue
        blob = blobs.get(Path(name).name)
        if blob is not None:
            target.write_bytes(blob.content)
            logger.info(
                "Rehydrated %s (%d bytes) for ingest %s",
                name,
                len(blob.content),
                job.id,
            )
            paths.append(str(target))
            continue
        if job.file_path and Path(job.file_path).exists():
            # Legacy job created before shared storage existed.
            paths.append(job.file_path)
            continue
        logger.error(
            "Ingest %s: file %s unrecoverable (no scratch/blob/legacy)", job.id, name
        )
        raise IngestFilesUnavailableError()
    return paths


def delete_files_for_job(db: Session, ingest_id: str) -> int:
    """Stage deletion of the job's blob rows. Caller commits."""
    return (
        db.query(IngestFile)
        .filter(IngestFile.ingest_id == ingest_id)
        .delete(synchronize_session=False)
    )


def cleanup_job_files(db: Session, job: IngestJob, local_paths: list[str]) -> None:
    """Terminal-state cleanup: blob rows + scratch files. Caller commits."""
    delete_files_for_job(db, str(job.id))
    for path in local_paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete scratch file %s", path)


def sweep_expired(db: Session, ttl_days: int = FILE_TTL_DAYS) -> int:
    """Delete blobs older than ttl_days (abandoned PENDING_REVIEW jobs). Commits."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    deleted = (
        db.query(IngestFile)
        .filter(IngestFile.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    if deleted:
        logger.info("ingest_files TTL sweep removed %d expired rows", deleted)
    return deleted
