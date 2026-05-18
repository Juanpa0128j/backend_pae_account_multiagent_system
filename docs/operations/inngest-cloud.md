# Inngest — Local Dev → Cloud → Production

The PAE backend uses Inngest as a durable workflow engine behind a feature flag
(`WORKFLOW_ENGINE=inngest`). This guide covers the three operating modes:

1. **Local-only** — Inngest Dev Server (no cloud account)
2. **Cloud branch env** — Inngest Cloud + local backend via ngrok
3. **Production** — Inngest Cloud + Render deploy

Always start with mode 1 for fast iteration. Promote to mode 2 before merging.

---

## 1. Local-only (Inngest Dev Server)

Use during day-to-day development. Zero cloud cost. No keys.

### Setup

```bash
make inngest-install   # downloads ./bin/inngest from GitHub release
```

In `.env`:
```
WORKFLOW_ENGINE=inngest
INNGEST_DEV=1
INNGEST_APP_ID=pae-backend
# Keys can be empty in dev mode
INNGEST_EVENT_KEY=
INNGEST_SIGNING_KEY=
```

### Run

```bash
# Terminal 1 — backend
uv run uvicorn main:app --reload

# Terminal 2 — Inngest dev server
make inngest-dev
```

Dashboard at `http://localhost:8288`. State is in-memory (lost on restart).

---

## 2. Cloud branch env (via ngrok)

Use to validate the cloud path before merging to main. Inngest Cloud
needs to reach your local backend, so you tunnel the dev machine through
ngrok and register the public URL in the dashboard.

### One-time Inngest Cloud setup

1. Sign up at https://app.inngest.com — GitHub OAuth, free Hobby tier
2. Dashboard → Apps → create app `pae-backend`
3. Settings → Environments → create a **branch environment** named
   `spike/inngest-workflows` (or whatever branch you're testing)
4. In that environment, Settings → Keys, copy:
   - `INNGEST_EVENT_KEY`
   - `INNGEST_SIGNING_KEY`

### Local config

In `.env`:
```
WORKFLOW_ENGINE=inngest
INNGEST_DEV=0
INNGEST_APP_ID=pae-backend
INNGEST_EVENT_KEY=signkey-branch-...
INNGEST_SIGNING_KEY=signkey-...
```

### Run

```bash
# Terminal 1 — backend
uv run uvicorn main:app --reload

# Terminal 2 — ngrok tunnel
make inngest-tunnel
# Copy the printed https URL, e.g. https://abc-123.ngrok-free.app

# In the Inngest dashboard (branch env) → Apps → Sync URL,
# paste: https://abc-123.ngrok-free.app/api/inngest

# Trigger a sync:
curl -X PUT https://abc-123.ngrok-free.app/api/inngest
```

Runs now appear in `https://app.inngest.com/env/<branch>/runs`.

### Caveats

- Free ngrok URLs rotate on every restart. Re-paste the URL each session, or
  pay for a reserved domain.
- The branch env is isolated from production — safe to break.

---

## 3. Production (Render)

### Render env vars

In the Render dashboard for the backend service, set:

```
WORKFLOW_ENGINE=inngest
INNGEST_DEV=0
INNGEST_APP_ID=pae-backend
INNGEST_EVENT_KEY=signkey-prod-...
INNGEST_SIGNING_KEY=signkey-prod-...
INNGEST_CONCURRENCY_PER_NIT=5
INNGEST_OPENAI_THROTTLE_RPM=400
```

`INNGEST_EVENT_KEY` / `INNGEST_SIGNING_KEY` come from the **Production**
environment in the Inngest dashboard (not the branch env).

### Sync on deploy

After Render deploys, register the backend with Inngest Cloud:

```bash
curl -X PUT https://your-backend.onrender.com/api/inngest
```

To automate, set up a Render deploy hook in the Inngest dashboard
(Apps → Sync new deploys).

### Production guard

`app/core/config.py` enforces that when `APP_ENV=production` and
`WORKFLOW_ENGINE=inngest`, both `INNGEST_EVENT_KEY` and `INNGEST_SIGNING_KEY`
are non-empty. Missing keys → startup fails fast.

---

## Free tier limits (Hobby)

- 50,000 function runs per month
- 25 functions per environment
- 1,000 concurrent steps
- 7-day trace history
- Branch environments included

Current PAE function count: **2** (`process-pipeline`, `ingest-pipeline`).

Estimated run usage for a single accountant demo:
- ~50 ingest events per day × 30 days = 1,500 runs/mo
- ~50 process events per day × 30 days = 1,500 runs/mo
- Plus HITL waits + audit-confirmed events: ~4× multiplier worst case
- Total ≈ 12,000 runs/mo

Headroom on free tier: ~38k runs. Upgrade trigger: more than two concurrent
production tenants or batch ingest spikes.

---

## What runs through Inngest today

| Function | Trigger event | Inner work |
|---|---|---|
| `process-pipeline` | `app/process.start` | `_run_process_job_impl` (LangGraph accounting) + HITL `wait_for_event` |
| `ingest-pipeline` | `app/ingest.start` | `_run_ingest_pipeline` (LangGraph ingest) via `asyncio.to_thread` |

Both wrap the existing LangGraph orchestrator as a single Inngest step.
LangGraph supervisor + agent nodes are not split into per-step retries —
that is a deliberate scope limit. Inngest provides:

- Durable retries on infrastructure failure
- Memoization across restarts
- Per-tenant concurrency (`Concurrency(key="event.data.company_nit")`)
- OpenAI rate-limit shield (`Throttle(key='"openai"', limit=400/60s)`)
- Double-dispatch protection (`Singleton(key="event.data.process_id")`)
- HITL durability via `ctx.step.wait_for_event` (1 hour timeout, Spanish auto-fail copy)

---

## Observability

LangSmith continues to handle LLM traces. Every Inngest step opens a
LangSmith parent span tagged with:

- `inngest_run_id`
- `inngest_event_id`
- `inngest_fn_id`

Filter LangSmith traces by these metadata keys to find the LLM activity for
a given Inngest run, or vice-versa.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `make inngest-dev` says binary missing | Never ran install | `make inngest-install` |
| Inngest UI shows no functions | Backend not running OR `WORKFLOW_ENGINE != inngest` | Check `.env`, restart backend |
| Cloud branch env shows no runs | Sync URL not registered | `curl -X PUT <ngrok>/api/inngest` |
| `ValidationError: INNGEST_EVENT_KEY must be set` on startup | Production guard fired with empty keys | Set keys in Render or set `APP_ENV=development` locally |
| `make inngest-tunnel` says ngrok missing | ngrok not installed | https://ngrok.com/download, then `ngrok config add-authtoken …` |
| HITL `wait_for_event` never resolves | `/audit-confirm` endpoint didn't emit event | Confirm `WORKFLOW_ENGINE=inngest`; check backend logs for the event-send line |
