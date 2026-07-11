# Ingest file shared storage — design

**Date:** 2026-07-11
**Status:** Approved
**Problem:** Uploaded ingest files are saved to local ephemeral disk (`/tmp/pae_uploads`, `app/api/v1/ingest.py:53`). The ingest pipeline pauses at PENDING_REVIEW between upload and classification-confirm. Any container restart (observed OOM kill in production, 2026-07-11 13:25 UTC) or multi-instance routing (horizontal scaling) means the instance that resumes the job does not have the file → `File not found` → job RECHAZADA. Reproduced in production logs.

## Decision

Store uploaded file bytes in the existing Postgres database (Supabase) — the only storage shared by all instances — for the lifetime of the ingest job. Local disk becomes parser scratch space only, never storage.

Rejected alternatives:
- **Supabase Storage / DO Spaces:** proper object stores, but require a new SDK, credentials, and bucket/RLS setup. Files only need to live minutes and run 1–5MB; the DB is already there. Upgrade path if files ever need to outlive jobs.
- **Eager parse at upload, store text, discard file:** extraction method depends on the *confirmed* doc type (`_EXTRACT_METHOD_MAP`, Vía A/B), and LlamaParse charges per page — eager parsing pays for jobs the user cancels or reclassifies.
- **Frontend re-sends file at confirm:** breaks on page reload, adds API surface and a frontend change.

## Schema

New table `ingest_files` (SQLAlchemy model + Alembic migration):

| column | type | notes |
|---|---|---|
| `id` | String(50) PK | |
| `ingest_id` | String(50) FK → `ingest_jobs.id`, `ON DELETE CASCADE`, indexed | |
| `file_name` | String(255) NOT NULL | original filename |
| `content` | LargeBinary NOT NULL | file bytes (TOAST) |
| `created_at` | DateTime NOT NULL | for TTL sweep |

## Write path — `upload_file` (`app/api/v1/ingest.py`)

- After existing magic-byte validation, insert one `ingest_files` row per file **in the same transaction** as the `IngestJob` row (rollback → zero orphans).
- **Remove** the upload-time `save_temp_file` local write. DB is the only storage.
- **New per-file size cap: 25MB → HTTP 422** with Spanish error copy (currently no cap exists). Bounds bytea row size and per-request memory.
- Piggybacked TTL sweep: `DELETE FROM ingest_files WHERE created_at < now() - interval '7 days'` (no cron infra; abandoned PENDING_REVIEW jobs are the only non-terminal leak path).

## Read path — `ensure_local_files(db, job) -> list[str]`

New helper, the single rehydration point. For each expected file of the job: if a local scratch path exists, use it; else fetch the blob from `ingest_files` and write it to scratch, named `{ingest_id}_{file_name}` (the prefix fixes a latent collision bug: today resume reconstructs paths by bare filename, so two jobs uploading `factura.pdf` overwrite each other).

Called at both resume points before the pipeline runs:
- `update_ingest_classification` (`ingest.py`, pre-`background_tasks.add_task`)
- Inngest ingest fn (`app/workflows/functions/ingest_pipeline.py:28`)

Fallbacks, in order: scratch file exists → use it; blob exists → rehydrate; legacy job (pre-deploy, no blob rows) → `job.file_path` as today; nothing → job FAILED with accountant-facing copy *"El archivo ya no está disponible; vuelva a subirlo."* (replaces today's generic pipeline abort).

Scratch files are deleted by the existing cleanup at pipeline end (`ingest.py:185`). The helper interface returns local paths so a future LlamaParse v2 migration (which can consume bytes directly) only changes helper internals, not callers.

## Delete path

- Pipeline terminal (COMPLETED/FAILED, `ingest.py:185` cleanup): delete the job's `ingest_files` rows alongside the existing scratch unlink.
- Cancel endpoint (`ingest.py:988` cleanup): same.
- Job row deletion: FK cascade.
- Backstop: 7-day TTL sweep (write path above). A confirm after sweep hits the "vuelva a subirlo" error explicitly.

## Error handling summary

| failure | outcome |
|---|---|
| upload fails mid-transaction | rollback, no orphan blobs |
| pipeline FAILED | terminal → blobs deleted |
| container crash / OOM mid-pipeline | job non-terminal, blob survives → re-confirm rehydrates on any instance (the fix) |
| blob + scratch + legacy path all missing | FAILED, "El archivo ya no está disponible; vuelva a subirlo." |
| file > 25MB | 422 at upload |

## Testing (TDD — failing tests first)

1. Resume with empty scratch dir succeeds via blob rehydration (the core regression test).
2. Two jobs with identical filenames don't cross-contaminate (prefix test).
3. Terminal state deletes the job's `ingest_files` rows.
4. Legacy job (no blob rows, has `file_path`) still resumes from local path.
5. Upload > 25MB per file → 422, no rows written.
6. TTL sweep deletes only rows older than 7 days.

Existing suite stays green (`make lint`, `make format`, `make test`).

## Scope

Backend only; API contract unchanged; frontend untouched. No new dependencies. ~1 model, 1 migration, 1 helper, 2 call-site edits, 2 cleanup-site edits, tests.

Out of scope (separate lanes): `ingest.py` async→def conversion; LlamaParse v2 SDK migration (see scratchpad audit 2026-07-11).
