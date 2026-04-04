# GitHub Actions CI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a CI workflow that runs unit tests and the full simulate pipeline on every PR push.

**Architecture:** One `.github/workflows/ci.yml` file with two sequential jobs: `unit-tests` (no secrets, runs offline) and `simulate-pipeline` (needs real Supabase + API keys, only runs if unit tests pass). Each simulate run uses a unique NIT (`CI-{run_id}-ViaA/ViaB`) to avoid DB conflicts between runs.

**Tech Stack:** GitHub Actions, uv (Python package manager), pytest, uvicorn, astral-sh/setup-uv action.

---

### Task 1: Create `.github/workflows/ci.yml`

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Create the directory**

```bash
mkdir -p .github/workflows
```

**Step 2: Write the workflow file**

Create `.github/workflows/ci.yml` with this exact content:

```yaml
name: CI

on:
  pull_request:
    branches:
      - "**"

jobs:
  # ─── Job 1: Unit tests — no secrets, runs fully offline ───────────────────
  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up uv
        uses: astral-sh/setup-uv@v4
        with:
          version: "latest"

      - name: Install dependencies
        run: uv sync

      - name: Run unit tests
        run: |
          uv run pytest tests/ -v --timeout=60 \
            --ignore=tests/e2e \
            --ignore=tests/features/test_supabase_pipeline_feature.py \
            -p no:warnings
        env:
          # Minimal env so imports don't crash on missing settings
          GEMINI_API_KEY: "dummy-for-unit-tests"
          OPENAI_API_KEY: "dummy-for-unit-tests"
          GROQ_API_KEY: ""
          DATABASE_URL: "postgresql://dummy:dummy@localhost:5432/dummy"
          LLAMA_CLOUD_API_KEY: "dummy-for-unit-tests"

  # ─── Job 2: Full simulate pipeline — runs only if unit tests pass ──────────
  simulate-pipeline:
    name: Simulate Pipeline (Via A + Via B)
    runs-on: ubuntu-latest
    needs: unit-tests

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up uv
        uses: astral-sh/setup-uv@v4
        with:
          version: "latest"

      - name: Install dependencies
        run: uv sync

      - name: Run database migrations
        run: uv run alembic upgrade head
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}

      - name: Start backend server
        run: |
          uv run uvicorn main:app --host 0.0.0.0 --port 8016 &
          echo $! > /tmp/uvicorn.pid
          echo "Backend PID: $(cat /tmp/uvicorn.pid)"
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          LLAMA_CLOUD_API_KEY: ${{ secrets.LLAMA_CLOUD_API_KEY }}
          HF_TOKEN: ${{ secrets.HF_TOKEN }}

      - name: Wait for backend to be ready
        run: |
          for i in $(seq 1 20); do
            if curl -sf http://127.0.0.1:8016/docs > /dev/null 2>&1; then
              echo "Backend is ready after ${i}s"
              break
            fi
            echo "Waiting... (${i}/20)"
            sleep 2
          done
          curl -sf http://127.0.0.1:8016/docs > /dev/null || (echo "Backend failed to start" && exit 1)

      - name: Run Via A simulation (demo documents)
        run: |
          uv run python scripts/simulate_frontend_full_pipeline.py \
            --base-url http://127.0.0.1:8016 \
            --source-mode demo \
            --company-nit "CI${{ github.run_id }}A" \
            --city Bogota \
            --ciiu 6920 \
            --timeout-seconds 300 \
            --poll-seconds 3 \
            --report-timeout-seconds 420
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}

      - name: Run Via B simulation (first-level uploads)
        run: |
          uv run python scripts/simulate_frontend_full_pipeline.py \
            --base-url http://127.0.0.1:8016 \
            --source-mode via-b \
            --company-nit "CI${{ github.run_id }}B" \
            --city Medellin \
            --ciiu 6910 \
            --timeout-seconds 180 \
            --poll-seconds 3
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}

      - name: Stop backend server
        if: always()
        run: |
          if [ -f /tmp/uvicorn.pid ]; then
            kill $(cat /tmp/uvicorn.pid) || true
          fi
```

**Step 3: Verify the file was created correctly**

```bash
cat .github/workflows/ci.yml | head -20
```

Expected: shows `name: CI` and `on: pull_request`

**Step 4: Commit**

```bash
git add .github/workflows/ci.yml .gitignore docs/plans/
git commit -m "ci: add GitHub Actions workflow for unit tests and simulate pipeline"
```

---

### Task 2: Configure GitHub Secrets

**This task is manual — done in the GitHub web UI, not in code.**

Go to: `https://github.com/Juanpa0128j/backend_pae_account_multiagent_system/settings/secrets/actions`

Add these repository secrets (click "New repository secret" for each):

| Secret Name | Value | Where to find it |
|-------------|-------|-----------------|
| `DATABASE_URL` | `postgresql://...` | Supabase → Project Settings → Database → Connection string (URI mode) |
| `GEMINI_API_KEY` | `AIza...` | Google AI Studio → API Keys |
| `OPENAI_API_KEY` | `sk-...` | platform.openai.com → API Keys |
| `LLAMA_CLOUD_API_KEY` | `llx-...` | cloud.llamaindex.ai → API Keys |
| `HF_TOKEN` | `hf_...` | huggingface.co → Settings → Access Tokens (optional, needed for RAG embeddings) |

**Verify secrets are set:**
```
GitHub UI → Settings → Secrets → Actions → should show 5 secrets listed
```

---

### Task 3: Push and verify CI runs

**Step 1: Push the branch**

```bash
git push origin asegurar-creacion-docs
```

**Step 2: Open or update a PR**

If no PR exists:
```bash
gh pr create --title "feat: second-level document generation" --body "See PR description"
```

If PR already exists, the push in Step 1 is enough to trigger the workflow.

**Step 3: Check the workflow runs**

```bash
gh run list --workflow=ci.yml --limit=3
```

Expected output:
```
STATUS    NAME   WORKFLOW  BRANCH                    EVENT         ID
in_progress  CI  ci.yml   asegurar-creacion-docs   pull_request  ...
```

**Step 4: Watch the unit-tests job**

```bash
gh run watch
```

Or open in browser:
```bash
gh run view --web
```

**Step 5: Verify expected outcomes**

Unit tests job:
- Should show: `422 passed, 28 skipped`
- Should NOT show any `FAILED` lines (other than the supabase one which is ignored)

Simulate pipeline job (runs after unit tests pass):
- Via A step: should end with `[OK] All 3 second-level documents generated successfully`
- Via B step: should end with `[OK] Via B: All 3 second-level documents derived successfully`

---

### Task 4: Fix unit-test env isolation (if needed)

**Context:** The unit tests use `MagicMock` and don't hit real APIs. However, some imports at module load time may crash if `DATABASE_URL` is malformed. The dummy values in Job 1 handle this, but if new imports are added later that validate env on startup, this step explains how to fix it.

If you see errors like `pydantic_settings ValidationError` or `sqlalchemy.exc.ArgumentError` in unit tests on CI:

**Option A:** Add `--override-ini="env=DATABASE_URL=..."` to pytest call.

**Option B:** Add a `conftest.py` override at the root:

```python
# tests/conftest.py  (add to existing file)
import os
os.environ.setdefault("DATABASE_URL", "postgresql://ci:ci@localhost/ci")
os.environ.setdefault("GEMINI_API_KEY", "dummy")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
```

**This task is only needed if CI fails due to env validation — skip it otherwise.**

---

## Verification Checklist

- [ ] `.github/workflows/ci.yml` exists and is committed
- [ ] All 5 GitHub secrets are set in the repository settings
- [ ] Workflow appears in the Actions tab after push
- [ ] `unit-tests` job passes with 422 passed / 28 skipped
- [ ] `simulate-pipeline` job starts only after unit-tests passes
- [ ] Via A simulate ends with `[OK] All 3 second-level documents generated`
- [ ] Via B simulate ends with `[OK] Via B: All 3 second-level documents derived`
- [ ] Both runs used unique NITs (`CI{run_id}A` and `CI{run_id}B`)
