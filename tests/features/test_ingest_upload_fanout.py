"""Tests for bulk fan-out in /api/v1/ingest/upload (AAA pattern)."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def fake_files():
    """Return three pseudo-PDF byte buffers."""
    return [
        ("doc1.pdf", BytesIO(b"%PDF-1.4 fake-content-one")),
        ("doc2.pdf", BytesIO(b"%PDF-1.4 fake-content-two")),
        ("doc3.pdf", BytesIO(b"%PDF-1.4 fake-content-three")),
    ]


def _settings(engine: str) -> SimpleNamespace:
    return SimpleNamespace(workflow_engine=engine, inngest_dev=True)


@pytest.mark.asyncio
async def test_fanout_documents_mode_inngest_creates_n_jobs(fake_files) -> None:
    # Arrange
    from app.api.v1 import ingest as ingest_mod

    created_jobs = []

    def _create_ingest_job(db, fname, path, **kwargs):
        job = SimpleNamespace(
            id=f"job-{len(created_jobs)}",
            status=SimpleNamespace(value="pending"),
            file_name=fname,
            created_at=None,
        )
        created_jobs.append(job)
        return job

    mock_dispatch = AsyncMock()

    with (
        patch.object(ingest_mod, "get_settings", return_value=_settings("inngest")),
        patch.object(ingest_mod, "dispatch_ingest_start", mock_dispatch),
        patch.object(
            ingest_mod.db_service, "create_ingest_job", side_effect=_create_ingest_job
        ),
        patch.object(ingest_mod.db_service, "set_company_locked_pathway"),
        patch.object(
            ingest_mod.db_service, "get_company_locked_pathway", return_value=None
        ),
        patch.object(
            ingest_mod, "save_temp_file", side_effect=lambda c, n: f"/tmp/{n}"
        ),
    ):
        # Build mocked UploadFile-like objects
        files = []
        for name, buf in fake_files:
            f = MagicMock()
            f.filename = name
            f.read = AsyncMock(return_value=buf.getvalue())
            files.append(f)

        # Act
        response = await ingest_mod.upload_file(
            request=MagicMock(),
            background_tasks=MagicMock(),
            files=files,
            company_nit="900123456",
            doc_type=None,
            parser_mode="fast",
            multi_file_mode="documents",
            db=MagicMock(),
            current_user=SimpleNamespace(id="u1"),
        )

    # Assert
    assert len(created_jobs) == 3
    assert mock_dispatch.await_count == 3
    assert response.ingest_id == "job-0"
    assert response.ingest_ids == ["job-0", "job-1", "job-2"]


@pytest.mark.asyncio
async def test_pages_mode_does_not_fanout(fake_files) -> None:
    # Arrange
    from app.api.v1 import ingest as ingest_mod

    created_jobs = []

    def _create_ingest_job(db, fname, path, **kwargs):
        job = SimpleNamespace(
            id=f"job-{len(created_jobs)}",
            status=SimpleNamespace(value="pending"),
            file_name=fname,
            created_at=None,
        )
        created_jobs.append(job)
        return job

    mock_bg = MagicMock()
    mock_dispatch = AsyncMock()

    with (
        patch.object(ingest_mod, "get_settings", return_value=_settings("inngest")),
        patch.object(ingest_mod, "dispatch_ingest_start", mock_dispatch),
        patch.object(
            ingest_mod.db_service, "create_ingest_job", side_effect=_create_ingest_job
        ),
        patch.object(ingest_mod.db_service, "set_company_locked_pathway"),
        patch.object(
            ingest_mod.db_service, "get_company_locked_pathway", return_value=None
        ),
        patch.object(
            ingest_mod, "save_temp_file", side_effect=lambda c, n: f"/tmp/{n}"
        ),
    ):
        files = []
        for name, buf in fake_files:
            f = MagicMock()
            f.filename = name
            f.read = AsyncMock(return_value=buf.getvalue())
            files.append(f)

        # Act
        await ingest_mod.upload_file(
            request=MagicMock(),
            background_tasks=mock_bg,
            files=files,
            company_nit="900123456",
            doc_type=None,
            parser_mode="fast",
            multi_file_mode="pages",
            db=MagicMock(),
            current_user=SimpleNamespace(id="u1"),
        )

    # Assert — pages mode keeps a single ingest_job
    assert len(created_jobs) == 1
    mock_dispatch.assert_not_called()
    mock_bg.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_inline_engine_does_not_fanout_even_in_documents_mode(fake_files) -> None:
    # Arrange
    from app.api.v1 import ingest as ingest_mod

    created_jobs = []

    def _create_ingest_job(db, fname, path, **kwargs):
        job = SimpleNamespace(
            id=f"job-{len(created_jobs)}",
            status=SimpleNamespace(value="pending"),
            file_name=fname,
            created_at=None,
        )
        created_jobs.append(job)
        return job

    mock_bg = MagicMock()
    mock_dispatch = AsyncMock()

    with (
        patch.object(ingest_mod, "get_settings", return_value=_settings("inline")),
        patch.object(ingest_mod, "dispatch_ingest_start", mock_dispatch),
        patch.object(
            ingest_mod.db_service, "create_ingest_job", side_effect=_create_ingest_job
        ),
        patch.object(ingest_mod.db_service, "set_company_locked_pathway"),
        patch.object(
            ingest_mod.db_service, "get_company_locked_pathway", return_value=None
        ),
        patch.object(
            ingest_mod, "save_temp_file", side_effect=lambda c, n: f"/tmp/{n}"
        ),
    ):
        files = []
        for name, buf in fake_files:
            f = MagicMock()
            f.filename = name
            f.read = AsyncMock(return_value=buf.getvalue())
            files.append(f)

        # Act
        await ingest_mod.upload_file(
            request=MagicMock(),
            background_tasks=mock_bg,
            files=files,
            company_nit="900123456",
            doc_type=None,
            parser_mode="fast",
            multi_file_mode="documents",
            db=MagicMock(),
            current_user=SimpleNamespace(id="u1"),
        )

        # Assert
    assert len(created_jobs) == 1
    mock_dispatch.assert_not_called()
    mock_bg.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_fanout_partial_dispatch_failure_marks_only_failed_job(
    fake_files,
) -> None:
    """If dispatch_ingest_start fails for one file, mark that job FAILED but keep others healthy."""
    # Arrange
    from app.api.v1 import ingest as ingest_mod
    from app.models.database import IngestStatus

    created_jobs = []

    def _create_ingest_job(db, fname, path, **kwargs):
        job = SimpleNamespace(
            id=f"job-{len(created_jobs)}",
            status=SimpleNamespace(value="pending"),
            file_name=fname,
            created_at=None,
        )
        created_jobs.append(job)
        return job

    dispatch_calls = []

    async def _dispatch_with_failure(**kwargs):
        dispatch_calls.append(kwargs)
        # Fail dispatch for the SECOND file only
        if kwargs["ingest_id"] == "job-1":
            raise RuntimeError("simulated Inngest unreachable")

    update_calls = []

    def _update_ingest_job(db, ingest_id, status, **kwargs):
        update_calls.append((ingest_id, status, kwargs))
        return SimpleNamespace(id=ingest_id, status=status)

    with (
        patch.object(ingest_mod, "get_settings", return_value=_settings("inngest")),
        patch.object(ingest_mod, "dispatch_ingest_start", _dispatch_with_failure),
        patch.object(
            ingest_mod.db_service, "create_ingest_job", side_effect=_create_ingest_job
        ),
        patch.object(
            ingest_mod.db_service,
            "update_ingest_job",
            side_effect=_update_ingest_job,
        ),
        patch.object(ingest_mod.db_service, "set_company_locked_pathway"),
        patch.object(
            ingest_mod.db_service, "get_company_locked_pathway", return_value=None
        ),
        patch.object(
            ingest_mod, "save_temp_file", side_effect=lambda c, n: f"/tmp/{n}"
        ),
    ):
        files = []
        for name, buf in fake_files:
            f = MagicMock()
            f.filename = name
            f.read = AsyncMock(return_value=buf.getvalue())
            files.append(f)

        # Act
        response = await ingest_mod.upload_file(
            request=MagicMock(),
            background_tasks=MagicMock(),
            files=files,
            company_nit="900123456",
            doc_type=None,
            parser_mode="fast",
            multi_file_mode="documents",
            db=MagicMock(),
            current_user=SimpleNamespace(id="u1"),
        )

    # Assert — all 3 jobs created, all 3 dispatches attempted
    assert len(created_jobs) == 3
    assert len(dispatch_calls) == 3

    # Exactly one update_ingest_job call: the failed dispatch (job-1) marked FAILED
    failed_updates = [u for u in update_calls if u[1] == IngestStatus.FAILED]
    assert len(failed_updates) == 1
    assert failed_updates[0][0] == "job-1"
    assert "encolar" in failed_updates[0][2]["extraction_errors"][0].lower()

    # Successful jobs (job-0, job-2) must NOT be touched
    other_updates = [u for u in update_calls if u[0] != "job-1"]
    assert other_updates == []

    # Response returns first job (job-0) — successfully dispatched
    assert response.ingest_id == "job-0"
