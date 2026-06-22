# Backend PAE Account Multiagent System

Backend for the PAE Account Multiagent System — a FastAPI application implementing a
multi-agent pipeline for automated Colombian accounting and tax compliance, backed by
a **Supabase pgvector RAG layer** for regulatory document retrieval.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Agent Pipelines](#agent-pipelines)
5. [RAG System](#rag-system)
6. [Setup](#setup)
7. [Environment Variables](#environment-variables)
8. [Supabase Demo](#supabase-demo-end-to-end)
9. [Running Tests](#running-tests)
10. [Database Migrations](#database-migrations)
11. [Key Dependencies](#key-dependencies)
12. [Design Principles](#design-principles)

---

## Overview

The system automates document ingestion, accounting classification, and tax compliance
validation for Colombian companies. It uses a supervisor-worker multi-agent architecture
orchestrated with **LangGraph**, with semantic document retrieval powered by **BAAI/bge-m3**
embeddings stored in **Supabase pgvector**.

Key features:
- **Hybrid RAG search**: Combines full-text search (PostgreSQL `tsvector`) with
  dense vector cosine similarity using Reciprocal Rank Fusion (RRF, k=60).
- **Cross-encoder reranking**: `BAAI/bge-reranker-v2-m3` re-scores candidates before
  returning results to the agents.
- **Multi-collection vector store**: Shared normativa collection (read-only) + per-company
  collections (read/write).
- **107-document normativa base**: 41 PUC accounts + 50 Estatuto Tributario articles +
  16 Ley 43/1990 PCGA principles, all indexed with 1024-dim BGE-M3 embeddings.
- **Supervisor FSM**: Routes three pipelines (ingest / process / reporting) with structured
  `agent_log` execution traces at every node.
- **Deterministic stage auditors**: Ingest, Contador, and Tributario now run rule-based
  audits that emit structured findings before/alongside LLM evaluation.
- **Self-improvement loop with guardrails**: Per-agent retry budgets and a global
  circuit breaker prevent infinite correction loops and produce an explicit give-up record.
- **Pre-persist integrity gate**: Process-mode persistence is blocked when pre-persist
  audit findings include `BLOCKER` severity, returning structured `audit_blocker` errors.
- **Accountant-facing trace endpoint**: `GET /api/v1/process/{process_id}/trace`
  returns a Spanish pipeline timeline with findings, blockers, and give-up context.
- **Ingest trace endpoint**: `GET /api/v1/ingest/{ingest_id}/trace`
  returns an ingest-focused Spanish timeline and extraction blockers when present.
- **Tax declaration drafts**: Pre-filled F300 (IVA), F350 (Retefuente), F110 (Renta PJ),
  and ICA municipal forms generated from journal entries for accountant review before filing.
- **DIAN 2026 calendar**: Obligation deadlines computed per NIT last digit with 30-day alerts.
- **Smart document classification**: `recibo_caja` (cash receipts) now captures `tipo_recibo` signal to intelligently route to 130505 (accounts receivable) vs 4xxx (income) accounts; includes referencia_factura linkage for cartera collections.
- **PDF rendering improvements**: Word-wrapped account descriptions in financial statements using ReportLab Paragraph; prevents text overflow in table cells for long Colombian account names.
- **Durable workflow layer (Inngest)**: Feature-flagged (`WORKFLOW_ENGINE=inngest`) durable execution wrapping both pipelines. Provides per-NIT concurrency fairness, cluster-wide OpenAI throttling, HITL audit-confirmation that survives backend restarts, bulk ingest fan-out for multi-document uploads, and LangSmith ↔ Inngest trace correlation. Default engine (`inline`) is unchanged.

---

## Architecture

```text
┌─────────────────────────────────────────────────────┐
│                  FastAPI (main.py)                  │
│  /api/v1/ingest  /api/v1/process  /api/v1/evaluate  │
└────────────────────────┬────────────────────────────┘
                         │
              ┌──────────▼───────────┐
              │  LangGraph Supervisor │
              │  (agents/graph.py)   │
              └──────────┬───────────┘
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    IngestAgent    ProcessAgent   PersistNode
          │              │
          └──────────────┘
                  │
                  ▼
            RAGService           ← Pydantic interface (app/services/rag_service.py)
                  │
                  ▼
       SupabaseVectorDB           ← SQLAlchemy + pgvector (app/core/vectordb.py)
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
  search()             search_hybrid()
  (pure vector)        (BM25 + vector, RRF)
       │                     │
       └──────────┬──────────┘
                  ▼
        Supabase PostgreSQL + pgvector
        ├── normativa_colombia_v1  (107 docs, read-only)
        └── empresa_{nit}_docs    (per-company, read/write)
```

**Retrieval pipeline for `search_normativo(hybrid=True)` (default):**

```text
query string
    │ embed_query()
    ▼
BGE-M3 (HF Inference API, 1024 dims)
    │
    ├─(fts_cte) plainto_tsquery('spanish', query) → ts_rank_cd → top-20 ranked by BM25
    └─(vec_cte) embedding <=> query_vec          → top-20 ranked by cosine distance
                         │
                    FULL OUTER JOIN + RRF (k=60)
                         │
                    top-10 candidates
                         │
              bge-reranker-v2-m3 (HF Inference API)
                         │
                    top-N final results (RAGResult[])
```

---

## Project Structure

```text
backend_pae_account_multiagent_system/
├── main.py                     # FastAPI application entry point
├── pyproject.toml              # Dependency management (uv)
├── alembic.ini                 # Alembic configuration
├── alembic/
│   └── versions/
│       ├── 8fb1b0855393_initial_schema.py
│       ├── c3f8a2d91b5e_add_vector_documents.py   # vector_documents table + HNSW index
│       └── d4e5f6a7b8c9_add_fts_column.py         # content_tsv tsvector + GIN index
├── app/
│   ├── api/v1/                 # Versioned REST endpoints (FastAPI routers)
│   ├── agents/                 # Multi-agent logic (LangGraph, Supervisor-Worker)
│   │   ├── graph.py            # LangGraph StateGraph definition
│   │   ├── ingest_agent.py     # Document ingestion worker
│   │   ├── persist_node.py     # Database persistence worker
│   │   └── state.py            # Shared AgentState schema
│   ├── core/
│   │   ├── config.py           # Pydantic settings (reads .env)
│   │   └── vectordb.py         # SupabaseVectorDB (search, search_hybrid, upsert, ...)
│   ├── models/                 # Pydantic request/response schemas
│   └── services/
│       └── rag_service.py      # RAGService (search_normativo, search_historico, ...)
├── data/
│   ├── puc_accounts.json       # 41 PUC account entries (classes 1–6)
│   ├── normativa_tributaria.json  # 50 Estatuto Tributario articles
│   └── ley_43_1990.json        # 16 Ley 43/1990 PCGA principles
├── docs/                       # Architecture docs and implementation status
├── scripts/
│   ├── populate_rag.py         # Seeds Supabase with all normativa documents
│   └── demo_supabase_process.py  # End-to-end demo against Supabase
├── tests/
│   ├── test_rag.py             # Core vector DB + RAG service tests (33 tests)
│   ├── test_rag_expanded.py    # Hybrid search + data integrity tests (35 tests)
│   ├── test_e2e_phase2.py      # Supervisor FSM, all pipelines, retry (17 tests)
│   ├── test_validation_system.py  # Schema validation engine (40 tests)
│   ├── test_agent_integration.py  # Agent pipeline integration tests
│   └── test_database.py        # ORM + db_service CRUD tests
└── .env.example                # Template for environment variables
```

### Folder Reference

| Folder | Purpose | How to Contribute |
| :--- | :--- | :--- |
| `app/api/` | Defines the REST interface. | Add new versioned routers and link them in `main.py`. Keep logic minimal; delegate to agents or services. |
| `app/agents/` | Orchestrates LLM-based agents via the Supervisor FSM. Each node emits structured `LogEntry` events to `agent_log`. | Implement new worker nodes or refine supervisor logic using LangGraph. Update the shared `AgentState` in `state.py`. |
| `app/models/` | Houses the data contracts. | Define Pydantic models for request/response validation. Always update these before changing API implementations. |
| `app/core/` | Cross-cutting concerns. | Configuration (`config.py`), Gemini client, vector store (`vectordb.py`). |
| `app/services/` | Deterministic logic + RAG. | `rag_service.py` for semantic search, `pdf_processor.py` for extraction, `validation_engine.py` for schema compliance. |
| `data/` | Static seed data. | PUC accounts and normativa articles used to populate the normativa vector collection. |
| `scripts/` | CLI utilities. | `populate_rag.py` seeds the Supabase normativa collection. `demo_supabase_process.py` runs an end-to-end demo. |

---

## Agent Pipelines

All pipelines are driven by the **unified 9-node graph** (`create_agent_graph()`), entered via the `supervisor` node. The `mode` field in `AgentState` controls routing.

```text
supervisor
  ├─[error]──────────────────────────────→ error_terminal → END
  ├─[mode=ingest]────────────────────────→ ingesta
  │                                           ↓
  │                                       validate_output
  │                                           ├─[retry]──→ ingesta
  │                                           ├─[error]──→ END
  │                                           └─[end]────→ db_persist → END
  ├─[mode=process]───────────────────────→ contador → supervisor
  │                                           ↓
  │                                       tributario → supervisor
  │                                           ↓
  │                                       auditor → supervisor
  │                                           ├─[approved]──────────────→ db_persist → END
  │                                           ├─[fixable findings]──────→ responsible agent (budgeted retry)
  │                                           └─[unfixable/budget hit]──→ error_terminal → END
  └─[mode=reporting]─────────────────────→ reportero → END
```

### Pipeline 1 — Ingest (`mode="ingest"`)
PDF upload → LlamaParse extraction → Gemini interpretation → schema validation (max 3 retries) → database persistence.

### Pipeline 2 — Process (`mode="process"`)
Staged transactions → Contador (PUC classification) → Tributario (tax calc) → Auditor (double-entry review) → budgeted correction loop or persistence.

Persist behavior in process mode:
- Before writing `TransactionPosted` and `JournalEntryLine`, `pre_persist_auditor` validates process integrity.
- If any `BLOCKER` finding exists, persistence is refused and process status is marked failed.
- API status/result surfaces structured error payloads with `error_category="audit_blocker"`.

### Pipeline 3 — Reporting (`mode="reporting"`)
Generates balance / P&L reports via the Reportero agent.

The chat endpoint (`POST /api/v1/chat/stream`) emits Server-Sent Events for
each step of the pipeline so the frontend can render an inline reasoning
panel similar to OpenAI / Anthropic / Gemini. Each `thinking` event carries
a `phase` (`intent` → `params` → `gathering_data` → `rag` → `generating` →
`complete`), a short Spanish label, optional detail and `duration_ms`. The
full trace is also persisted on `chat_messages.reasoning` (JSONB) so loaded
sessions reproduce the panel.

### Financial Statements Matrix (Direct vs Derived)

| Source document | Target statement | Mode |
|---|---|---|
| `balance_general` upload (Vía B) | Balance General | `direct` |
| `balance_general_anterior` upload (Vía B) | Balance General (período anterior) | `direct` — stored as `statement_type='balance_general'` with prior period; used by NIC 7 indirect method |
| `estado_resultados` upload (Vía B) | Estado de Resultados | `direct` |
| `libro_auxiliar` upload (Vía B) | Libro Auxiliar | `direct` |
| `flujo_de_caja` upload (Vía B) | Flujo de Caja | `direct` |
| `cambios_patrimonio` upload (Vía B) | Cambios en Patrimonio | `direct` |
| `notas_estados_financieros` upload (Vía B) | Notas a los EEFF | `direct` |
| Persisted `balance_general` + `estado_resultados` + `libro_auxiliar` (same company + period) | Flujo de Caja | `derived` |
| Persisted `balance_general` + `estado_resultados` + `libro_auxiliar` (same company + period) | Cambios en Patrimonio | `derived` |
| Persisted `balance_general` + `estado_resultados` + `libro_auxiliar` (same company + period) | Notas a los EEFF | `derived` |

Rules:
- Preferred mode for `flujo_de_caja`, `cambios_patrimonio`, and `notas_estados_financieros` is `derived`.
- If required inputs (`balance_general`, `estado_resultados`, `libro_auxiliar`) are missing for the selected company and period, the system returns a clear business error (no automatic fallback).
- Direct fallback remains available only when the user explicitly uploads one of those target documents through Vía B.
- Derived outputs are tracked with explicit lineage links in `financial_statement_lineage`.

> See `docs/IMPLEMENTATION_STATUS.md` for the full Phase 3 roadmap.

---

## RAG System

### Collections

| Collection | Documents | Access |
|---|---|---|
| `normativa_colombia_v1` | 41 PUC + 50 ET articles + 16 Ley 43/1990 PCGA = **107 docs** | Read-only (seeded by script) |
| `empresa_{nit}_docs` | Company-specific invoices, receipts, and financial documents | Read/Write (per NIT) |

### Normativa Coverage

| Source | Count | Key topics |
|---|---|---|
| Plan Único de Cuentas (PUC, Decreto 2650/1993) | 41 | Account codes 1105–6205 |
| Estatuto Tributario (ET) | 50 | Renta, IVA, Retención, Sanciones, Precios de transferencia |
| Ley 43 de 1990 | 16 | PCGA (12 principles Arts. 35–46), Contador público, Revisor fiscal |

### RAGService Methods

| Method | Signature | Purpose |
|---|---|---|
| `search_normativo` | `(query, n_results=5, hybrid=True)` | PUC + ET + Ley 43 regulatory search |
| `search_historico` | `(nit, query="", n_results=3)` | Company document history search |
| `add_empresa_doc` | `(nit, text, metadata=None)` | Store new company document |
| `rerank` | `(query, docs, top_n=3)` | Cross-encoder reranking via bge-reranker-v2-m3 |

### Dashboard Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/dashboard/stats` | GET | Top-level aggregated metrics (pending docs, processed transactions, alerts, balance totals) |
| `/api/v1/dashboard/financial-summary` | GET | Complete financial overview (balance sheet, P&L, cash, tax payables, recent activity) |
| `/api/v1/dashboard/monthly-trend` | GET | Monthly ingresos vs gastos trend data; accepts `company_nit` (optional) and `months` (1–24, default 6) for chart rendering |

### Seeding the Normativa Collection

```bash
# First-time setup (or after data changes):
python scripts/populate_rag.py

# Force full re-index (deletes existing and rebuilds):
python scripts/populate_rag.py --force
```

Expected output after full seed: **~107 documents** indexed
(41 PUC + 50 ET + 16 Ley 43).

### HuggingFace Inference API Endpoints

| Purpose | Model | Endpoint path |
|---|---|---|
| Embeddings | `BAAI/bge-m3` | `/pipeline/feature-extraction` |
| Reranking | `BAAI/bge-reranker-v2-m3` | `/pipeline/text-ranking` |

Base URL: `https://router.huggingface.co/hf-inference/models/{model}`

Reranker payload: `{"inputs": [[query, doc1], [query, doc2], ...]}`
Reranker response: `{"scores": [float, ...]}`

---

## Setup

### Option 1: Dev Container (Recommended)

```bash
# 1. (Host shell, OUT of devcontainer) — start local Postgres+pgvector
make db-up

# 2. Open in VS Code, click "Reopen in Container".

# 3. (Inside devcontainer) — bootstrap everything in one shot
make dev-bootstrap
# This runs: db-up + alembic upgrade head + seed_puc + populate_rag (3-5 min)

# 4. (Inside devcontainer) — start backend with logs
mkdir -p logs && uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000 2>&1 | tee -a "logs/backend-$(date +%Y%m%d).log"
```

**`.env`** in devcontainer must use `host.docker.internal`:
```
DATABASE_URL=postgresql://pae:pae@host.docker.internal:5433/pae
```

### Option 2: Local Setup (host uvicorn, no devcontainer)

```bash
# 1. Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install Python dependencies
UV_LINK_MODE=copy uv sync

# 3. Copy and fill in environment variables
cp .env.example .env
# Edit .env: set DATABASE_URL=postgresql://pae:pae@localhost:5433/pae (for local DB)
#            + HUGGINGFACE_API_KEY, GEMINI_API_KEY, LLAMA_CLOUD_API_KEY

# 4. Start DB (in host shell)
make db-up

# 5. Apply migrations + seed (PUC + RAG)
source .venv/bin/activate
alembic upgrade head
python scripts/seed_puc.py        # 84 PUC accounts
python scripts/populate_rag.py    # 107 normativa docs (3-5 min)

# 6. Start the development server
uvicorn main:app --reload
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL connection string (synchronous; e.g. `postgresql://...`). Supabase Postgres is used in production; local dev uses the docker-compose container. |
| `CLERK_ISSUER` | ✅ | Clerk issuer URL (e.g. `https://your-instance.clerk.accounts.dev`) — used to verify RS256 JWTs |
| `CLERK_JWKS_URL` | ✅ | Clerk JWKS endpoint (e.g. `https://your-instance.clerk.accounts.dev/.well-known/jwks.json`) |
| `HUGGINGFACE_API_KEY` | ✅ | HuggingFace Inference API key (BGE-M3 embeddings + reranker) |
| `GEMINI_API_KEY` | ✅ | Google AI API key (Gemini fallback LLM) |
| `LLAMA_CLOUD_API_KEY` | ✅ | LlamaCloud API key for PDF parsing |
| `OPENAI_API_KEY` | | OpenAI key — enables GPT-4.1-nano/mini as primary LLM (Gemini becomes fallback) |
| `GROQ_API_KEY` | | Groq key — third fallback in LLM chain |
| `LANGSMITH_API_KEY` | | LangSmith observability (optional) |
| `LANGSMITH_TRACING` | | Set `true` to enable LangSmith tracing |
| `APP_ENV` | | `development` (default) or `production` |
| `SECRET_KEY` | | Secret key for production boot validation; must be ≥32 chars in `production` |
| `ALLOWED_ORIGINS` | | Comma-separated CORS origins for production |
| `WORKFLOW_ENGINE` | | `inline` (default) or `inngest` — selects durable workflow engine |
| `INNGEST_EVENT_KEY` | | Inngest Cloud event key (`evt-...`) — required when `WORKFLOW_ENGINE=inngest` |
| `INNGEST_SIGNING_KEY` | | Inngest signing key (`signkey-test-...` or `signkey-prod-...`) |
| `INNGEST_DEV` | | `true` for local dev server, `false` for Inngest Cloud |
| `INNGEST_IS_PRODUCTION` | | Override signature verification mode — set `true` when using ngrok + Inngest Cloud locally |

Copy `.env.example` to `.env` and fill in the values. Never commit `.env` to version control.

---

## Supabase Demo (End-to-End)

This repository includes a deterministic demo that runs the **unified agent graph** against a real Supabase PostgreSQL database, exercising both the ingest and process pipelines sequentially.

1. Set your Supabase Postgres URL in `.env`:

```bash
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
```

2. Apply migrations:

```bash
uv run alembic upgrade head
```

3. Run the demo:

```bash
uv run python scripts/demo_supabase_process.py
```

The script generates a PDF document, ingests it through the ingest pipeline (Pipeline 1), then triggers the process pipeline (Pipeline 2) and verifies final persistence (`transactions_posted`, `journal_entry_lines`) in Supabase.

---

## Running Tests

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Full test suite:
pytest tests/ -v

# Individual test files:
pytest tests/test_rag.py -v                       # 33 tests — Core vector DB + RAG service
pytest tests/test_rag_expanded.py -v              # 35 tests — Hybrid search + data integrity
pytest tests/test_e2e_phase2.py -v                # 17 tests — Supervisor FSM, all pipelines, retry
pytest tests/test_validation_system.py -v         # 40 tests — Schema validation engine
pytest tests/test_agent_integration.py -v         # Agent pipeline integration tests
pytest tests/test_database.py -v                  # ORM + db_service CRUD tests

# Run a single test class:
pytest tests/test_rag_expanded.py::TestDataFileIntegrity -v
```

All tests use an in-memory `FakeSupabaseVectorDB` / `ExtendedFakeDB` with deterministic
SHA-256-seeded embeddings. No HuggingFace API calls are made during testing.

---

## Database Migrations

Migrations are managed with **Alembic**. The schema runs against three
distinct Postgres databases depending on environment:

| Environment | Where | How |
|---|---|---|
| **Local development** | `docker-compose.dev.yml` (Postgres 16 + pgvector, port 5433) | `make db-up && make db-migrate` |
| **CI / pull requests** | Ephemeral `pgvector/pgvector:pg16` service in GitHub Actions | Runs `alembic upgrade head` per workflow invocation against a fresh DB |
| **Production** | Supabase Postgres (project URL in `DATABASE_URL`) | `alembic upgrade head` after merge |

This separation prevents the "all PRs share one prod DB" problem: each
developer iterates locally, each PR validates against an ephemeral CI
database, and only merged-to-main migrations touch Supabase.

### Local development workflow

```bash
make db-up         # Start the local Postgres+pgvector container (port 5433)
make db-migrate    # Apply all migrations to the local DB
make db-shell      # Open a psql shell on the local DB
make db-reset      # Destroy + recreate + remigrate (when schemas drift)
make db-down       # Stop the container (data preserved)
```

Set `DATABASE_URL=postgresql://pae:pae@localhost:5433/pae` in your `.env`
when working locally; switch back to the Supabase URL only when you need
to validate against production data.

### Concurrent migrations from parallel PRs

If two PRs each generate a new migration on top of the same parent, alembic
ends up with multiple heads and the next `alembic upgrade head` against
prod will fail. CI guards against this with `make migrate-check-heads`,
which runs before tests and fails the build until the second author
rebases their migration onto the merged one.

### Common commands

```bash
# Apply all pending migrations:
alembic upgrade head

# Check current migration state:
alembic current

# Generate a new migration:
alembic revision --autogenerate -m "description"

# Verify a single head exists (CI gate):
make migrate-check-heads

# Rollback one step:
alembic downgrade -1
```

### Migration History

| Revision | Description |
|---|---|
| `8fb1b0855393` | Initial schema |
| `c3f8a2d91b5e` | `vector_documents` table + HNSW index + B-tree index on `collection_name` |
| `d4e5f6a7b8c9` | `content_tsv` GENERATED tsvector column + GIN index (enables hybrid search) |
| `85397898945d` | `tax_declaration_drafts` table + `es_declarante` column on `company_settings` |

---

## Deployment

The backend is deployed on **DigitalOcean App Platform**. Supabase continues to host the PostgreSQL database and pgvector RAG layer. Authentication is provided by **Clerk** (RS256 JWT verification via JWKS).

### DigitalOcean App Platform

1. Ensure `app.yaml` is at repo root and configured with your GitHub repo.
2. Set environment variables in the DO Dashboard (see [Environment Variables](#environment-variables)).
3. Deploy via dashboard or `doctl apps create --spec app.yaml`.
4. Migrations run automatically via the `PRE_DEPLOY` job.

See [`MIGRATION.md`](MIGRATION.md) for a full migration guide from Render.

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | REST API framework |
| `langgraph` | Multi-agent orchestration (StateGraph) |
| `langchain-google-genai` | Google Gemini LLM integration |
| `llama-parse` | PDF parsing via LlamaCloud |
| `sqlalchemy` | ORM / raw SQL execution against Supabase |
| `pgvector` / `alembic` | pgvector SQLAlchemy type + schema migrations |
| `huggingface-hub` | HuggingFace Inference API client (BGE-M3 embeddings) |
| `httpx` | Async HTTP client (bge-reranker-v2-m3 API call) |
| `pydantic-settings` | Typed environment variable configuration |
| `uv` | Fast Python package manager and virtual environment tool |

---

## Design Principles

1. **Contract-first**: Define Pydantic schemas before implementing endpoints.
2. **Dev containers**: Use devcontainers, linters, formatters, and LSP for all development.
3. **Branch-based workflow**: Feature branches + pull requests into `main`.
4. **Environment isolation**: Use `.env` files, never commit secrets.
5. **Meaningful names and minimal comments**: Code should be self-documenting; add comments only where logic is non-obvious.
6. **Review AI output carefully**: All AI-generated code must be reviewed before merging.
7. **Short, clear documentation**: Document the *what* and *why*, not the *how*.
8. **Simplify and divide**: Prefer small, focused modules over monolithic files.

---

## Known limitations

### Multi-page upload — file order matters

When uploading multiple pages of the same document in `pages` mode (the default
`multi_file_mode`), the order of files in the upload payload matters. The LLM
extractor concatenates pages in the order received and needs to see the header
+ items page first, then totals/continuation pages.

Recommended naming and upload order: `<doc>.<ext>` for the main page,
`<doc>-2.<ext>`, `<doc>-3.<ext>` for follow-ups. Upload them in that order.

If the totals page (e.g. `FV 192-2.jpg`) is uploaded before the header page
(e.g. `FV 192.jpg`), extraction degenerates: `items=[]` and the IVA
calculation falls back to computing 19% of the total (instead of 19% of the
subtotal), producing a CxC entry with double-IVA and the wrong income
account. Reordering the pages fixes it without code changes.

A permanent fix would be a natural sort of `file_paths` in
`app/agents/ingest_agent.py` before the concat loop (~10 LOC); not implemented
yet by design.

### Bank statements require `parser_mode=premium`

For `extracto_bancario` uploads, the default `fast` and `gpt4o` LlamaParse
modes produce unreliable OCR on multi-column bank tables: they invert
debit/credit signs, fabricate spurious rows (e.g. inventing a $22M "ABONO
INTERESES AHORROS" that does not exist in the source PDF), and drop large
movements. Always pick `premium` in the upload UI for this doc type.

Premium parses ~5–10× slower than `fast` but reconciles cleanly: sum of
extracted movements matches the statement's `total_abonos + total_cargos`
within OCR-cent tolerance, intereses generados match the `valor intereses
pagados` line, and saldo_inicial + Σ creditos − Σ debitos equals saldo_final.

Cache keys now embed the parser mode (`<sha256>.<mode>.md`), so switching
between fast / premium / gpt4o forces a fresh parse instead of silently
returning the previous mode's output. See [`app/agents/ingest_agent.py`](app/agents/ingest_agent.py).

### `cuenta_cobro` — IVA siempre 0, retención condicional

Cuentas de cobro are issued by natural persons not obliged to invoice and
not IVA-responsible. The pipeline enforces:

- `totales.total_iva = 0` always (regardless of OCR output).
- If the document text says *"no aplicar retención según artículo X ET"* (or
  equivalent), the ingest prompt sets
  `informacion_adicional.aplicar_retencion = false`. Tributario respects this
  flag and zeroes both retefuente and reteICA.
- Below the 27-UVT monthly minimum ($1,414,098 in 2026 at UVT=$52,374),
  retefuente is zeroed automatically even without the flag. Tributario uses
  `BASE_MINIMA_RETEFUENTE_UVT` per `tipo_transaccion` (servicios=4 UVT,
  bienes/arrendamiento=27 UVT) and `BASE_MINIMA_RETEICA_UVT=4 UVT`.
- Contador maps the expense account by concept: honorarios contables /
  asesoría / outsourcing → 511505 (or 511595), comisiones → 511510,
  servicios técnicos → 511525, arrendamientos → 511525/5140. Account 5305
  (Gastos Financieros) is explicitly forbidden for CC docs.

### Multi-transaction documents loop the contador per movement

Bank statements, tax payment receipts with `conceptos` tables, and any other
mapper that returns more than one `raw_transaction` now call
`extract_contador_output` **once per movement** instead of sending the whole
batch in a single LLM call. The single-call path remains for single-tx docs
(facturas, CC, DS, CE).

Without the loop the LLM lazily returns a single asiento pair and
`persist_node._asientos_for_tx` clones it across every pending transaction,
producing N posted rows with identical (wrong) accounting. With the loop
each movement gets its own classification, RAG context, and journal entry.
See [`app/agents/contador_agent.py`](app/agents/contador_agent.py).
