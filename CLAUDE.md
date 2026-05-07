# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Context

**PAE — Proyecto de Aplicación Específica (Universidad Nacional de Colombia)**

**Title:** Plataforma de asistencia contable autónoma para el manejo de cuentas y generación de informes contables

**Goal:** An LLM-driven agentic system that automates the full Colombian accounting cycle with precision comparable to a human accountant. Quality is validated via an agentic evaluator and, where possible, by a human CPA.

**Team:** Juan Pablo Mejía, Mateo Builes, Samuel Castaño, Jhon Edison Pinto

**Regulatory frame:** Estatuto Tributario colombiano (DIAN), Ley 43/1990, Ley 1314/2009, NIIF Plenas (Decreto 2420/2015), PUC colombiano.

## Commands

**Package manager:** `uv` — never use pip directly.

```bash
# Setup
uv sync
alembic upgrade head
python scripts/populate_rag.py      # Seed RAG vector store (skip if exists)
python scripts/populate_rag.py --force  # Force re-index

# Development server
uvicorn main:app --reload

# Tests
make test                                            # Full suite (e2e excluded)
make test-file FILE=tests/test_rag.py                # Single file
make test-class FILE=tests/test_rag.py CLASS=TestDataFileIntegrity  # Single class

# Lint / format
make lint                                            # ruff check — must be 0 errors
make lint-fix                                        # auto-fix ruff errors
make format                                          # ruff format + black

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"
```

## After Every File Modification

**MANDATORY — run in order after any code change:**

```bash
make lint     # must report 0 errors before proceeding
make format   # auto-formats; re-read any file you formatted before editing further
make test     # must pass with no new failures
```

You are forbidden from reporting a task complete until all three pass.

## Architecture

Multi-agent FastAPI backend for Colombian accounting automation. Processes financial documents through a LangGraph-orchestrated pipeline.

### Request Flow

```
FastAPI (main.py) → /api/v1/* routers
    → LangGraph StateGraph (agents/graph.py)
        → Supervisor FSM (agents/supervisor.py)
            → Worker nodes: Ingest → Contador → Tributario → Auditor → Reportero → Persist
    → RAGService (services/rag_service.py)
        → SupabaseVectorDB (core/vectordb.py) — pgvector, HNSW, hybrid BM25+vector via RRF
        → PostgreSQL (Supabase)
```

### Three Pipelines

1. **Ingesta:** PDF/XLSX upload → LlamaParse → Gemini extraction → schema validation (3 retries) → DB persist
2. **Procesamiento:** Pending transactions → Contador (PUC classification) → Tributario (IVA/retención) → Auditor (double-entry) → budgeted feedback loop or persist
3. **Reportería:** Balance sheets, P&L, cash flow via Reportero agent

### Accountant Trace

- `GET /api/v1/process/status/{process_id}` now exposes `has_warnings` and `trace_url`
- `GET /api/v1/process/{process_id}/trace` returns a Spanish `PipelineTrace`
- `GET /api/v1/ingest/{ingest_id}/trace` returns an ingest-focused Spanish `PipelineTrace`
- Process persistence is blocked on pre-persist `BLOCKER` findings and surfaces `error_category="audit_blocker"`

### Dashboard Aggregations

- `GET /api/v1/dashboard/monthly-trend` returns monthly ingresos vs gastos via `db_service.get_monthly_totals_by_class()` (class 4 = revenue/ingresos, class 5 = expenses/gastos). Accepts `company_nit` (optional, normalized) and `months` (1–24, default 6). Responds with `MonthlyTrendResponse` containing month labels in Spanish.

### Key Module Roles

| Path | Role |
|------|------|
| `app/agents/state.py` | `AgentState` TypedDict — 90+ fields shared across all nodes |
| `app/agents/graph.py` | StateGraph with 10 nodes and conditional edges |
| `app/agents/supervisor.py` | Routing logic based on `state["mode"]`: `ingest`, `process`, `reporting` |
| `app/core/config.py` | Pydantic Settings; loads `.env` (GEMINI_API_KEY, DATABASE_URL, etc.) |
| `app/core/gemini_client.py` | Backward-compatibility re-export shim — all extraction logic lives in `llm_client.py`. Do not add code here. |
| `app/core/vectordb.py` | pgvector wrapper; `search()` (vector) and `search_hybrid()` (BM25+vector, RRF k=60) |
| `app/services/rag_service.py` | High-level RAG interface: `search_normativo`, `search_historico`, `add_empresa_doc` |
| `app/models/database.py` | SQLAlchemy ORM: `CompanySettings`, `CuentaPUC`, `IngestJob`, `TransactionPosted`, `JournalEntryLine`, `FinancialStatement`, etc. |
| `app/services/financial_statement_service.py` | Derives `FinancialStatement` rows from journal entries; `list_financial_statements`, `derive_financial_statements` |
| `app/services/nit_utils.py` | Colombian NIT normalization helpers: `normalize_nit`, `normalize_optional_nit` |
| `app/core/llm_client.py` | Multi-provider LLM client with OpenAI → Gemini → Groq fallback chain |
| `app/models/audit.py` | Structured audit schemas: `AuditFinding`, `AuditReport`, `GiveUpRecord` |
| `app/models/trace.py` | Accountant-facing `PipelineTrace` / `TraceStep` response models |
| `app/services/audit_messages_es.py` | Spanish copy for accountant-visible audit findings and step summaries |
| `app/services/pipeline_trace_service.py` | Read-only derivation of `PipelineTrace` from `ProcessJob.agent_log` |
| `app/services/retencion_table.py` | Full 2026 retention table (UVT=$52.374, 28 concepts). Source: Carolina García, H&G Abogados |
| `app/services/tax_declaration_service.py` | Generates pre-filled F300/F350/F110/ICA drafts from journal entries; persists as `TaxDeclarationDraft` |
| `app/services/tax_calendar_service.py` | DIAN 2026 deadlines per NIT last digit; `list_obligations()` returns sorted `CalendarEntry` list |
| `app/api/v1/dashboard.py` | Dashboard aggregations: `/stats` (top-level KPIs), `/financial-summary` (complete overview), `/monthly-trend` (ingresos/gastos by month) |
| `data/` | Static seed data: 41 PUC accounts, 50 Estatuto Tributario articles, 16 Ley 43/1990 entries |

### LLM Usage Convention

Always use `LLMClient` (`app/core/llm_client.py`) as the single interface for all LLM calls. Never import or instantiate providers directly. The client handles the OpenAI → Gemini → Groq fallback chain automatically.

- Get the client: `from app.core.llm_client import get_llm_client; llm = get_llm_client()`
- Structured output schemas live in `app/models/llm_schemas.py` — do not add schemas to `gemini_client.py`
- Name the client variable `llm`, not `gemini` or any provider-specific name
- `app/core/gemini_client.py` is a backward-compat shim — do not add code there

### Tech Stack

- **LLM:** Multi-provider fallback — OpenAI GPT-4o-mini (primary if key set) → Google Gemini 2.5 Flash → Groq (`langchain-openai`, `langchain-google-genai`, `langchain-groq`)
- **Agent orchestration:** LangGraph StateGraph
- **Embeddings:** BAAI/bge-m3 (1024 dims) via HuggingFace Inference API
- **Reranking:** BAAI/bge-reranker-v2-m3
- **PDF parsing:** LlamaParse (LlamaCloud) + pypdf
- **Vector DB:** Supabase PostgreSQL + pgvector (HNSW index + GIN for FTS)
- **ORM/Migrations:** SQLAlchemy 2.0 + Alembic

### Document Types

The system handles 13+ Colombian financial document types (facturas, extractos bancarios, declaraciones de IVA, retención en la fuente, etc.). Document classification is in `app/services/doc_classifier.py` and `app/models/document_types.py`.

### RAG System

107 regulatory documents indexed at startup: 41 PUC accounts + 50 Estatuto Tributario articles + 16 Ley 43/1990 PCGA principles. Run `scripts/populate_rag.py` once to seed.

## Code Conventions

- **Imports:** stdlib → external → internal. Never wildcard imports.
- **Error handling:** Fail fast. Set `state["error"]` and return early — do not swallow exceptions.
- **State mutations:** All nodes receive and return `AgentState`. Never mutate shared objects outside the node.
- **DB sessions:** Always open with `SessionLocal()`, wrap in try/except/finally, close in `finally`. Prefer a single `db.commit()` per operation — avoid partial transactions.
- **LLM calls:** Use `get_llm_client()` for all LLM invocations; it handles provider fallback (OpenAI → Gemini → Groq) automatically.
- **NIT validation:** Colombian NITs must be cleaned (strip `.` and spaces) before storing. Reject empty strings.
- **PUC fallback:** When defaulting to account `519595`, emit an explicit `logger.warning`.
- **Corrected tax liability accounts (2026):** Retefuente por pagar = `2365` (not `240815`); ReteICA por pagar = `2368` (not `236540`); ICA gasto administración = `511505`; ICA gasto ventas = `521505`; Retenciones recibidas = `135518`/`135515`.

## Testing

- Tests live in `tests/` organized by layer:
  - `tests/agents/` — unit tests per agent node
  - `tests/features/` — integration tests per feature slice
  - `tests/core/` — unit tests for core modules (LLM client, Gemini client)
  - `tests/services/` — unit tests for service modules
  - `tests/e2e/` — end-to-end tests (excluded from `make test`; run via `make test-e2e`)
- Fixtures are in `tests/conftest.py`.
- External dependencies (DB, Gemini, LlamaParse) must be mocked in unit tests. Mock `app.agents.persist_node._auto_derive_statements` in process pipeline tests to avoid hitting the real DB for financial statement derivation.
- Run `make test` and confirm all tests pass before marking work complete.
- Do not add tests for behavior that doesn't exist yet (no speculative coverage).

## Git Rules

- Never commit unless explicitly asked. Show `git diff --stat` and a draft message first.
- Never push unless explicitly asked.
- Never force-push, `git reset --hard`, or `git clean -f` without explicit instruction.
- Never commit `.env`, secrets, or API keys.
- One concern per commit.

## Operational Notes

- `seed_company.py` and `scripts/seed_ci_settings.py` populate the test NIT
  `800999888`. They MUST NOT run in production. Verified absent from
  `main.py`, `app/core/*`, `render.yaml`, and Dockerfile startup paths.
- `scripts/dev/` contains developer-only utilities with synthetic data
  (`FakeLlamaParse`, `FakeGeminiClient`, simulated pipelines). Never invoke
  from CI, production startup, or the FastAPI app.
