# Hosting Architecture Decision

## System Architecture Diagram

```mermaid
flowchart TB
    subgraph CLIENT["Client Layer"]
        FE["Frontend App"]
    end

    subgraph DO["DigitalOcean App Platform (FastAPI)"]
        direction TB
        API["FastAPI\nmain.py"]

        subgraph ROUTERS["API Routers /api/v1/*"]
            R1["ingest"]
            R2["process"]
            R3["reports"]
            R4["tax"]
            R5["dashboard"]
            R6["auth"]
            R7["chat · puc · books\ntransactions · settings\nevaluation"]
        end

        subgraph PIPELINE["LangGraph StateGraph"]
            SUP["Supervisor\n(FSM router)"]
            ING["Ingesta\nLlamaParse + LLM extract"]
            VAL["validate_output"]
            CNT["Contador\nPUC classification"]
            TRI["Tributario\nIVA / Retención"]
            AUD["Auditor\ndouble-entry check"]
            REP["Reportero\nfinancial reports"]
            PER["db_persist"]
            IMP["import_existing"]
            ERR["error_terminal"]
            REV["review_terminal"]
            ART["audit_review_terminal"]
        end

        WFLOW["Workflow Dispatch\ndispatch.py"]
        INNWH["/api/inngest\nwebhook endpoint"]
    end

    subgraph INNGEST["Inngest Cloud (SaaS)"]
        IQ["Event Queue"]
        IC["Concurrency &\nThrottle Control"]
        IHITL["HITL Audit Gate\n(1h timeout)"]
    end

    subgraph SUPABASE["Supabase"]
        PG["PostgreSQL\n+ pgvector"]
        AUTH["Auth (JWT)"]
        RAG["RAG Index\n107 regulatory docs\npgvector HNSW"]
    end

    subgraph EXTERNAL["External APIs"]
        LLM["LLMClient\nOpenAI → Gemini → Groq\n(fallback chain)"]
        HF["HuggingFace\nBAI/bge-m3 embeddings\nbge-reranker-v2-m3"]
        LP["LlamaParse\nPDF extraction"]
        LS["LangSmith\nobservability"]
    end

    %% Client → API
    FE -->|"HTTP"| API
    API --> ROUTERS
    API --> WFLOW

    %% Workflow dispatch → Inngest
    WFLOW -->|"app/process.start\napp/ingest.start"| IQ
    IQ --> IC
    IC --> IHITL
    IC -->|"POST /api/inngest"| INNWH

    %% Inngest → Pipeline
    INNWH --> SUP

    %% LangGraph edges
    SUP -->|ingest| ING
    SUP -->|process| CNT
    SUP -->|reporting| REP
    SUP -->|import| IMP
    SUP -->|error| ERR
    ING --> VAL
    VAL -->|valid| SUP
    VAL -->|invalid| ERR
    CNT --> SUP
    TRI --> SUP
    AUD --> SUP
    SUP -->|persist| PER
    SUP -->|review| REV
    SUP -->|audit_review| ART
    IMP --> PER
    REP --> PER

    %% Persist → Supabase
    PER --> PG
    ING --> PG

    %% Auth
    API --> AUTH

    %% RAG
    ING -->|"search_normativo\nsearch_historico"| RAG
    CNT --> RAG
    RAG --> PG

    %% External LLM calls
    ING --> LLM
    CNT --> LLM
    TRI --> LLM
    AUD --> LLM
    REP --> LLM
    ING --> LP
    ING --> HF

    %% Observability
    PIPELINE -.->|"traces"| LS
    INNWH -.->|"inngest_run_id\nmetadata"| LS
```

**Date:** 2026-05-28  
**Status:** Decided — pending migration from Render free tier

---

## Current Problem

Render free tier spins down after 15 minutes of inactivity. When Inngest Cloud calls back the `/api/inngest` webhook, the server is asleep → webhook times out → pipeline never completes. Additionally, `WORKFLOW_ENGINE` is not set in `render.yaml`, so it defaults to `inline` — meaning all LangGraph pipelines run inside the FastAPI process itself. Multiple concurrent document uploads collapse the server.

---

## Target Architecture

```
Frontend
    ↓
[DigitalOcean App Platform] — FastAPI, always-on, $5–12/mo
    ↓ dispatches event
[Inngest Cloud] — free tier (3M steps/mo), external SaaS
    ↓ calls back webhook (never times out — DO never sleeps)
[DigitalOcean App Platform] — LangGraph pipeline executes
    ↓
[Supabase] — PostgreSQL + pgvector + Auth (stays as-is, free tier)
```

### Component Responsibilities

**DigitalOcean App Platform**
- Runs FastAPI 24/7, never sleeps
- Serves HTTP requests from frontend
- Exposes `/api/inngest` webhook endpoint for Inngest callbacks
- Executes LangGraph pipelines when Inngest triggers them
- Runs `alembic upgrade head` on each deploy

**Inngest Cloud**
- Receives events (`app/process.start`, `app/ingest.start`)
- Enforces per-NIT concurrency (max 5 pipelines per company)
- Enforces OpenAI throttle (400 req/min cluster-wide)
- Deduplicates double-dispatched jobs
- Retries failed pipeline steps
- Holds HITL audit confirmation gate (up to 1h, survives restarts)
- Provides dashboard for inspecting every run

**Supabase** (unchanged)
- PostgreSQL + pgvector for all data and RAG
- Auth (JWT)
- No migration needed — `DATABASE_URL` stays the same

---

## Cost

| Service | Cost |
|---------|------|
| DO App Platform (1 web service, Basic) | $5–12/mo |
| Inngest Cloud | $0 (free tier: 3M steps/mo) |
| Supabase | $0 (free tier) |
| **Total** | **$5–12/mo** |

DigitalOcean GitHub Education credit: **$200** (~16–40 months free).

---

## Why Not Render

| | Render Free | DO App Platform ($5/mo) |
|--|--|--|
| Sleeps after inactivity | Yes (15 min) | No |
| Inngest callbacks work | No (timeouts) | Yes |
| Always-on HTTP | No | Yes |
| Cost | $0 | $5/mo (covered by DO credits) |

Render paid tier ($7/mo) would also fix the sleep problem but wastes the $200 DO credits.

---

## Migration Steps

1. Create DO App Platform app — connect GitHub repo (`main` branch)
2. Copy all env vars from Render dashboard to DO dashboard
3. Add missing env vars:
   ```
   WORKFLOW_ENGINE=inngest
   INNGEST_EVENT_KEY=<from Inngest Cloud dashboard>
   INNGEST_SIGNING_KEY=<from Inngest Cloud dashboard>
   INNGEST_IS_PRODUCTION=true
   INNGEST_DEV=false
   ```
4. Update `ALLOWED_ORIGINS` to include new DO app URL
5. Create Inngest Cloud account if not done — set serve URL to `https://<your-app>.ondigitalocean.app/api/inngest`
6. Verify first deploy: health check at `/health`, test one document ingest end-to-end

No code changes required. Pure config migration.

---

## Production Upgrade Path

| Stage | Setup |
|-------|-------|
| Academic/demo (now) | DO App Platform Basic + Inngest free tier |
| Production v1 | DO App Platform Professional + Inngest free/paid |
| Production v2 | Evaluate Temporal (self-hosted) if Inngest limits hit or vendor lock-in becomes concern |

---

## Related Files

- `render.yaml` — current Render config (reference for env vars)
- `app/workflows/dispatch.py` — Inngest dispatch logic
- `app/workflows/inngest_client.py` — Inngest client singleton
- `app/workflows/functions/process_pipeline.py` — process Inngest function
- `app/workflows/functions/ingest_pipeline.py` — ingest Inngest function
- `app/core/config.py` — `workflow_engine`, `INNGEST_*` settings
- `main.py:170` — Inngest serve mount (activated when `workflow_engine == "inngest"`)
