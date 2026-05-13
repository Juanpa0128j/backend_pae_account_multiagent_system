-- PAE Pipeline Diagnostic Queries
-- Run inside the devcontainer with:
--     psql "$DATABASE_URL" -f scripts/diagnose_pending_transactions.sql
-- Or via make db-shell (local only):
--     \i scripts/diagnose_pending_transactions.sql

\echo '========================================'
\echo '1. PENDING transactions per company'
\echo '========================================'
SELECT
    company_nit,
    COUNT(*) AS cnt
FROM transactions_pending
WHERE status = 'PENDING'
GROUP BY company_nit
ORDER BY cnt DESC;

\echo ''
\echo '========================================'
\echo '2. ProcessJobs for pending ingest_ids'
\echo '========================================'
SELECT
    pj.id,
    pj.ingest_id,
    pj.status,
    LEFT(pj.error_message, 120) AS error_snippet,
    pj.created_at,
    pj.current_agent,
    pj.current_stage
FROM process_jobs pj
WHERE pj.ingest_id IN (
    SELECT ingest_id
    FROM transactions_pending
    WHERE status = 'PENDING'
)
ORDER BY pj.created_at DESC
LIMIT 50;

\echo ''
\echo '========================================'
\echo '3. IngestJob status for pending transactions'
\echo '========================================'
SELECT
    ij.id,
    ij.status AS ingest_status,
    ij.document_type,
    ij.file_name,
    ij.company_nit,
    ij.created_at
FROM ingest_jobs ij
WHERE ij.id IN (
    SELECT ingest_id
    FROM transactions_pending
    WHERE status = 'PENDING'
)
ORDER BY ij.created_at DESC
LIMIT 50;

\echo ''
\echo '========================================'
\echo '4. ProcessJob status distribution'
\echo '========================================'
SELECT status, COUNT(*) AS cnt
FROM process_jobs
GROUP BY status
ORDER BY cnt DESC;

\echo ''
\echo '========================================'
\echo '5. Old RUNNING/QUEUED jobs (>1 hour)'
\echo '========================================'
SELECT
    id,
    ingest_id,
    status,
    current_agent,
    current_stage,
    created_at,
    started_at
FROM process_jobs
WHERE status IN ('RUNNING', 'QUEUED')
  AND created_at < NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC;

\echo ''
\echo '========================================'
\echo '6. Overall pipeline health snapshot'
\echo '========================================'
SELECT
    (SELECT COUNT(*) FROM transactions_pending WHERE status = 'PENDING') AS pending_txn,
    (SELECT COUNT(*) FROM transactions_pending WHERE status = 'POSTED') AS posted_txn,
    (SELECT COUNT(*) FROM transactions_posted) AS total_posted,
    (SELECT COUNT(*) FROM process_jobs WHERE status = 'COMPLETED') AS proc_completed,
    (SELECT COUNT(*) FROM process_jobs WHERE status = 'FAILED') AS proc_failed,
    (SELECT COUNT(*) FROM process_jobs WHERE status = 'RUNNING') AS proc_running,
    (SELECT COUNT(*) FROM process_jobs WHERE status = 'PENDING_AUDIT_REVIEW') AS proc_audit_review,
    (SELECT COUNT(*) FROM process_jobs WHERE status = 'QUEUED') AS proc_queued,
    (SELECT COUNT(*) FROM journal_entry_lines) AS journal_lines;

\echo ''
\echo '========================================'
\echo '7. Failed ProcessJobs for pending txns'
\echo '========================================'
SELECT
    id,
    ingest_id,
    status,
    error_message,
    created_at,
    current_agent,
    current_stage
FROM process_jobs
WHERE status = 'FAILED'
  AND ingest_id IN (
      SELECT ingest_id
      FROM transactions_pending
      WHERE status = 'PENDING'
  )
ORDER BY created_at DESC
LIMIT 20;

\echo ''
\echo '========================================'
\echo '8. Pending txns with NO ProcessJob at all'
\echo '========================================'
SELECT
    tp.id AS pending_id,
    tp.ingest_id,
    tp.company_nit,
    tp.created_at,
    ij.status AS ingest_status,
    ij.file_name
FROM transactions_pending tp
JOIN ingest_jobs ij ON tp.ingest_id = ij.id
LEFT JOIN process_jobs pj ON ij.id = pj.ingest_id
WHERE tp.status = 'PENDING'
  AND pj.id IS NULL
ORDER BY tp.created_at DESC
LIMIT 30;
