# GitHub Actions CI Design

**Date:** 2026-04-03

## Goal

Run automated CI on every PR push: unit tests first, then full end-to-end simulation against real Supabase.

## Trigger

`on: pull_request` — fires on every push to any branch that has an open PR.

## Architecture: Two sequential jobs in one workflow

```
.github/workflows/ci.yml
│
├── job: unit-tests          (no secrets needed, runs offline with mocks)
│   └── pytest tests/ --ignore=tests/e2e --ignore=test_supabase_pipeline
│
└── job: simulate-pipeline   (needs: unit-tests)
    ├── uvicorn main:app --port 8016 &
    ├── simulate --source-mode demo   --company-nit CI-{run_id}-ViaA
    └── simulate --source-mode via-b  --company-nit CI-{run_id}-ViaB
```

Job 2 only runs if Job 1 passes, saving API credits on broken code.

## Job 1: unit-tests

- **Runner:** ubuntu-latest
- **Python:** 3.11
- **Package manager:** uv (astral-sh/setup-uv action)
- **Command:** `uv run pytest tests/ -v --timeout=60 --ignore=tests/e2e --ignore=tests/features/test_supabase_pipeline_feature.py`
- **Secrets:** none
- **Expected:** 422 passed, 28 skipped

## Job 2: simulate-pipeline

- **Runner:** ubuntu-latest
- **needs:** unit-tests
- **Steps:**
  1. `uv sync`
  2. `uv run alembic upgrade head`
  3. `uv run uvicorn main:app --port 8016 &`
  4. `sleep 8` (wait for startup)
  5. `uv run python scripts/simulate_frontend_full_pipeline.py --source-mode demo --company-nit CI-${{ github.run_id }}-ViaA --timeout-seconds 300 --poll-seconds 3`
  6. `uv run python scripts/simulate_frontend_full_pipeline.py --source-mode via-b --company-nit CI-${{ github.run_id }}-ViaB --timeout-seconds 180 --poll-seconds 3`

- **NIT isolation:** Each run uses `CI-{run_id}-ViaA` / `CI-{run_id}-ViaB`. Data accumulates in Supabase but doesn't interfere between runs.

## Required GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `DATABASE_URL` | Supabase PostgreSQL connection string |
| `OPENAI_API_KEY` | LLM classifier (doc_classifier.py) |
| `GEMINI_API_KEY` | LLM extraction (ingest agents) |
| `LLAMA_CLOUD_API_KEY` | PDF parsing via LlamaParse |
| `HF_TOKEN` | HuggingFace embeddings (optional — RAG) |

Set at: `github.com/Juanpa0128j/backend_pae_account_multiagent_system → Settings → Secrets → Actions`

## File to create

`.github/workflows/ci.yml`
