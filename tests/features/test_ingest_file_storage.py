"""Feature tests for shared ingest file storage (ingest_files table)."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import ingest as ingest_api
from app.models.database import Base, IngestFile, IngestJob, IngestStatus
from app.services import ingest_file_service as ifs


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_job(db, ingest_id="ing_test_1", file_name="factura.pdf"):
    job = IngestJob(
        id=ingest_id,
        file_name=file_name,
        file_path=None,
        status=IngestStatus.PENDING_REVIEW,
    )
    db.add(job)
    db.commit()
    return job


class TestIngestFileModel:
    def test_roundtrip_bytes(self, db):
        _make_job(db)
        row = IngestFile(
            id="ingf_1",
            ingest_id="ing_test_1",
            file_name="factura.pdf",
            content=b"%PDF-1.4 fake bytes",
        )
        db.add(row)
        db.commit()

        loaded = db.query(IngestFile).filter_by(ingest_id="ing_test_1").one()
        assert loaded.content == b"%PDF-1.4 fake bytes"
        assert loaded.file_name == "factura.pdf"
        assert loaded.created_at is not None


def _store(db, ingest_id, name, content=b"%PDF-1.4 x"):
    ifs.store_files(db, ingest_id, [(name, content)])
    db.commit()


class TestEnsureLocalFiles:
    def test_rehydrates_from_blob_when_scratch_missing(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        job = _make_job(db, "ing_rehydrate", "factura.pdf")
        _store(db, "ing_rehydrate", "factura.pdf", b"%PDF-1.4 blob-bytes")

        paths = ifs.ensure_local_files(db, job)

        assert len(paths) == 1
        assert paths[0].endswith("ing_rehydrate_factura.pdf")
        assert Path(paths[0]).read_bytes() == b"%PDF-1.4 blob-bytes"

    def test_same_filename_two_jobs_no_cross_contamination(
        self, db, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        job_a = _make_job(db, "ing_a", "factura.pdf")
        job_b = _make_job(db, "ing_b", "factura.pdf")
        _store(db, "ing_a", "factura.pdf", b"%PDF-1.4 AAA")
        _store(db, "ing_b", "factura.pdf", b"%PDF-1.4 BBB")

        paths_a = ifs.ensure_local_files(db, job_a)
        paths_b = ifs.ensure_local_files(db, job_b)

        assert Path(paths_a[0]).read_bytes() == b"%PDF-1.4 AAA"
        assert Path(paths_b[0]).read_bytes() == b"%PDF-1.4 BBB"
        assert paths_a[0] != paths_b[0]

    def test_legacy_job_without_blobs_falls_back_to_file_path(
        self, db, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        legacy_file = tmp_path / "legacy.pdf"
        legacy_file.write_bytes(b"%PDF-1.4 legacy")
        job = _make_job(db, "ing_legacy", "legacy.pdf")
        job.file_path = str(legacy_file)
        db.commit()

        paths = ifs.ensure_local_files(db, job)

        assert paths == [str(legacy_file)]

    def test_nothing_recoverable_raises(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        job = _make_job(db, "ing_lost", "gone.pdf")
        job.file_path = str(tmp_path / "gone.pdf")  # never written
        db.commit()

        with pytest.raises(ifs.IngestFilesUnavailableError):
            ifs.ensure_local_files(db, job)


class TestDeleteAndSweep:
    def test_delete_files_for_job(self, db):
        _make_job(db, "ing_del", "a.pdf")
        _store(db, "ing_del", "a.pdf")

        deleted = ifs.delete_files_for_job(db, "ing_del")
        db.commit()

        assert deleted == 1
        assert db.query(IngestFile).filter_by(ingest_id="ing_del").count() == 0

    def test_sweep_deletes_only_expired(self, db):
        _make_job(db, "ing_old", "old.pdf")
        _make_job(db, "ing_new", "new.pdf")
        _store(db, "ing_old", "old.pdf")
        _store(db, "ing_new", "new.pdf")
        db.query(IngestFile).filter_by(ingest_id="ing_old").update(
            {"created_at": datetime.now(timezone.utc) - timedelta(days=8)}
        )
        db.commit()

        swept = ifs.sweep_expired(db, ttl_days=7)

        assert swept == 1
        assert db.query(IngestFile).filter_by(ingest_id="ing_new").count() == 1
        assert db.query(IngestFile).filter_by(ingest_id="ing_old").count() == 0


class TestResumeRehydration:
    def test_resume_paths_resolve_with_empty_scratch_dir(
        self, db, tmp_path, monkeypatch
    ):
        """The production bug: instance restarted / different instance,
        /tmp is empty, job must still resume from DB blobs."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        job = _make_job(db, "ing_resume", "extracto.pdf")
        job.file_names = ["extracto.pdf"]
        db.commit()
        _store(db, "ing_resume", "extracto.pdf", b"%PDF-1.4 survived")

        # Scratch dir is empty — nothing was ever written locally here.
        paths = ifs.ensure_local_files(db, job)

        assert Path(paths[0]).read_bytes() == b"%PDF-1.4 survived"

    def test_unrecoverable_resume_raises_spanish_error(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        job = _make_job(db, "ing_gone2", "gone.pdf")
        with pytest.raises(ifs.IngestFilesUnavailableError) as exc_info:
            ifs.ensure_local_files(db, job)
        assert "vuelva a subirlo" in exc_info.value.detail


class TestUploadSizeCap:
    def test_reject_file_over_25mb(self):
        with pytest.raises(HTTPException) as exc_info:
            ingest_api.enforce_size_cap(b"x" * (25 * 1024 * 1024 + 1), "big.pdf")
        assert exc_info.value.status_code == 422
        assert "25MB" in exc_info.value.detail

    def test_accept_file_at_25mb(self):
        ingest_api.enforce_size_cap(b"x" * (25 * 1024 * 1024), "ok.pdf")  # no raise


class TestFirstRunRehydration:
    def test_first_pipeline_run_rehydrates_scratch_from_blobs(
        self, db, tmp_path, monkeypatch
    ):
        """The gap this branch fixes: the FIRST pipeline run after upload must
        not trust dispatch-time scratch paths — this instance may not be the
        one that wrote them (restart / horizontal scaling). Wipe scratch
        (nothing ever written locally here) and confirm
        `_run_ingest_pipeline` rehydrates from the DB blob before the graph
        sees any path. Does NOT mock `ensure_local_files` or `store_files` —
        only the graph entrypoint and the DB session routing are stubbed.
        """
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        job = _make_job(db, "ing_first_run", "factura.pdf")
        job.file_names = ["factura.pdf"]
        db.commit()
        _store(db, "ing_first_run", "factura.pdf", b"%PDF-1.4 first-run-bytes")

        dispatch_path = Path(ifs.scratch_path("ing_first_run", "factura.pdf"))
        assert not dispatch_path.exists()  # never written on this "instance"

        # Route the module's SessionLocal() calls at the same sqlite engine
        # the fixture uses, so rehydration sees the blob staged above.
        test_session_local = sessionmaker(bind=db.get_bind())
        monkeypatch.setattr(ingest_api, "SessionLocal", test_session_local)

        received: dict = {}

        def _fake_invoke_pipeline(file_path, initial_state=None, file_paths=None):
            paths = file_paths or [file_path]
            received["paths"] = paths
            # Record existence rather than assert here — an assertion raised
            # inside this stub would be swallowed by _run_ingest_pipeline's
            # broad except-Exception handler, masking a real regression.
            received["existed"] = [Path(p).exists() for p in paths]
            return {"error": None}

        monkeypatch.setattr(ingest_api, "invoke_ingest_pipeline", _fake_invoke_pipeline)

        ingest_api._run_ingest_pipeline(
            [str(dispatch_path)], "ing_first_run", None, "fast", "pages"
        )

        assert received.get("paths"), "pipeline stub was never invoked"
        assert all(received["existed"]), (
            f"first run received unreadable paths: {received['paths']}"
        )
        assert Path(received["paths"][0]).read_bytes() == b"%PDF-1.4 first-run-bytes"


class TestTerminalCleanup:
    def test_cleanup_job_files_removes_blobs_and_scratch(
        self, db, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        job = _make_job(db, "ing_done", "done.pdf")
        _store(db, "ing_done", "done.pdf")
        scratch = Path(ifs.scratch_path("ing_done", "done.pdf"))
        scratch.write_bytes(b"scratch")

        ifs.cleanup_job_files(db, job, [str(scratch)])
        db.commit()

        assert db.query(IngestFile).filter_by(ingest_id="ing_done").count() == 0
        assert not scratch.exists()


class TestScratchSizeCheck:
    def test_scratch_size_mismatch_rehydrates_from_blob(
        self, db, tmp_path, monkeypatch
    ):
        """When scratch exists but size doesn't match blob, rehydrate from blob."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        job = _make_job(db, "ing_size_check", "corrupt.pdf")
        blob_content = b"%PDF-1.4 real-content-here-with-many-bytes"
        _store(db, "ing_size_check", "corrupt.pdf", blob_content)

        # Create scratch with wrong size
        scratch = Path(ifs.scratch_path("ing_size_check", "corrupt.pdf"))
        scratch.write_bytes(b"truncated")  # 9 bytes vs 41 bytes in blob

        paths = ifs.ensure_local_files(db, job)

        assert len(paths) == 1
        assert Path(paths[0]).read_bytes() == blob_content
