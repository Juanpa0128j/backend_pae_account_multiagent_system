# AGENTS.md

Guidance for AI agents working in this repository.

## Project

**PAE — Universidad Nacional de Colombia**
**Title:** Plataforma de asistencia contable autónoma para el manejo de cuentas y generación de informes contables

An LLM-driven multi-agent system that automates the full Colombian accounting cycle with precision comparable to a human accountant. Output quality is validated by an agentic evaluator and, where possible, by a human CPA.

**Regulatory frame:** Estatuto Tributario colombiano (DIAN) · Ley 43/1990 · Ley 1314/2009 · NIIF Plenas (Decreto 2420/2015) · PUC colombiano.

**Team:** Juan Pablo Mejía · Mateo Builes · Samuel Castaño · Jhon Edison Pinto

## Scope & Implementation Status

When working on a feature, check this table first to understand what's expected, what's done, and what's pending.

| Capability | Description | Status |
|---|---|---|
| **Ingesta — Vía A** | PDF/XLSX/XML source docs → Gemini extraction → journal entries | ✅ Implemented |
| **Ingesta — Vía B** | Pre-existing financial statements (up to 4 per period: BG, ER, LA + optional BG anterior for NIC 7) → stored as `FinancialStatement` | ✅ Implemented |
| **Clasificación de documentos** | LLM classifies 13+ Colombian doc types | ✅ Implemented |
| **Contador** | PUC account classification per transaction | ✅ Implemented |
| **Tributario** | IVA, retención en la fuente, ICA calculations | ✅ Implemented |
| **Auditor** | Double-entry validation + internal control alerts | ✅ Implemented |
| **Reportero** | Balance general, P&L, libro auxiliar, flujo de caja, cambios patrimonio, notas financieras | ✅ Implemented |
| **Exportable reports** | PDF/Excel export of financial reports | ✅ Implemented |
| **Tax compliance assistant** | Preliminary tax values + deadline reminders (IVA, renta, retenciones, ICA) | ⏳ Pending |
| **Financial analysis** | KPIs: rentabilidad, liquidez, eficiencia, endeudamiento, rotaciones | ⏳ Pending |
| **Financial projections** | Income/expense/cash flow forecasts from historical data | ⏳ Pending |
| **Agentic evaluator** | Automated quality validation vs reference cases | ⏳ Pending |
| **Bank reconciliation** | Cuentas por cobrar/pagar conciliation support | ⏳ Pending |

### Specific Objectives (PAE)

1. **Data & preparation** — Representative Colombian transaction dataset; functional requirements and quality criteria.
2. **Modular architecture** — ETL pipelines + storage + decoupled agentic LLM modules; scalability, security, auditability.
3. **Pipeline implementation** — Excel/PDF/XML ETL with integrity validation; transaction classification and journal entries per Colombian law.
4. **Validation & benchmarking** — Metrics and test cases comparing system output vs human CPA and reference cases.
5. **Documentation & delivery** — Technical document + user manual.

## Package Manager

Use `uv` exclusively. Never call `pip` directly.

```bash
uv sync                          # Install all dependencies
uv add <package>                 # Add a dependency
```

## Essential Commands

```bash
# Environment setup
uv sync
alembic upgrade head
python scripts/populate_rag.py   # Seed RAG vector store (skip if already seeded)

# Development server
uvicorn main:app --reload

# Tests
make test                                                 # Full suite (e2e excluded)
make test-file FILE=tests/features/test_database_feature.py  # Single file
make test-class FILE=tests/agents/test_ingest_agent.py CLASS=TestIngestNode  # Single class

# Lint / format (run before committing)
make lint        # ruff check — must report 0 errors
make lint-fix    # auto-fix ruff errors
make format      # ruff format + black

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "short description"
# IMPORTANT: inspect the generated file before committing — autogenerate
# may pick up unrelated schema drift. Only include changes relevant to your PR.
make migrate-check-heads  # CI gate: fails if alembic has >1 head

# Local Postgres+pgvector (docker-compose.dev.yml, port 5433)
make db-up                # start local DB
make db-migrate           # alembic upgrade head against local
make db-reset             # destroy volume + remigrate
make db-shell             # psql shell on local DB
```

### Database environments

The schema runs against three Postgres instances:

- **Local (dev):** docker-compose pgvector container, point
  `DATABASE_URL=postgresql://pae:pae@localhost:5433/pae` in `.env`.
- **CI (pull requests):** ephemeral `pgvector/pgvector:pg16` service in
  GitHub Actions; migrations run fresh per workflow.
- **Production:** Supabase Postgres; only merged-to-main migrations touch it.

When two PRs each add a migration on top of the same parent, alembic ends
up with multiple heads. CI runs `make migrate-check-heads` before tests
and fails until the second author rebases. Never merge a PR with multiple
heads.

## Architecture

Multi-agent FastAPI backend for Colombian accounting automation. Documents are processed through a LangGraph StateGraph pipeline.

### Request Flow

```
FastAPI (main.py) → /api/v1/* routers
    → LangGraph StateGraph (app/agents/graph.py)
        → Supervisor FSM (app/agents/supervisor.py)
            → Worker nodes: Ingest → Contador → Tributario → Auditor → Reportero → Persist
    → RAGService (app/services/rag_service.py)
        → SupabaseVectorDB (app/core/vectordb.py) — pgvector + BM25 hybrid via RRF
        → PostgreSQL (Supabase)
```

### Three Pipelines

| Pipeline | Trigger | Flow |
|----------|---------|------|
| **Ingesta** | `state["mode"] = "ingest"` | Upload → parse (LlamaParse/openpyxl/xml_parser) → classify → Gemini extraction → validate → DB persist |
| **Procesamiento** | `state["mode"] = "process"` | Pending transactions → Contador (PUC) → Tributario (IVA/retención) → Auditor (double-entry) → Persist |
| **Reportería** | `state["mode"] = "reporting"` | Reportero agent → balance sheet / P&L / cash flow |

### Ingesta: Two Pathways

- **Vía A (`build_from_scratch`):** Source documents (facturas, extractos, declaraciones) → full accounting pipeline
- **Vía B (`work_with_existing`):** Pre-existing financial statements (balance_general, **balance_general_anterior**, estado_resultados, libro_auxiliar, flujo_de_caja, cambios_patrimonio, notas_estados_financieros, libro_diario) → routed through ingesta for typed extraction, then stored as `FinancialStatement` in DB. `balance_general_anterior` is remapped to `statement_type='balance_general'` at persist time (distinguished by period); `_load_prior_balance` in `financial_statement_service.py` finds it via `period_end < period_start`. `.xlsx` files classified as extracto_bancario are forced to Vía A.

### Supported Document Formats

| Extension | Parser |
|-----------|--------|
| `.pdf`, `.jpg`, `.jpeg`, `.png` | LlamaParse |
| `.xlsx` | `app/services/excel_parser.py` (openpyxl) |
| `.xml` | `app/services/xml_parser.py` (DIAN UBL 2.1) |

### Key Modules

| Path | Role |
|------|------|
| `app/agents/state.py` | `AgentState` TypedDict — 90+ fields shared across all nodes |
| `app/agents/graph.py` | StateGraph: 10 nodes, conditional edges |
| `app/agents/supervisor.py` | Entry node — validates file, classifies doc, sets mode/pathway |
| `app/agents/ingest_agent.py` | Format-aware extraction + Gemini dispatch by doc type |
| `app/agents/persist_node.py` | DB persistence for Vía A (transactions) and Vía B (financial statements); auto-derives statements via `_auto_derive_statements` |
| `app/agents/import_existing_node.py` | Vía B node — skips accounting pipeline |
| `app/core/config.py` | Pydantic Settings; loads `.env` |
| `app/core/gemini_client.py` | Gemini 2.5 Flash; one extraction method per document type |
| `app/core/llm_client.py` | Multi-provider LLM client with OpenAI → Gemini → Groq fallback chain |
| `app/core/vectordb.py` | pgvector wrapper; `search()` and `search_hybrid()` (RRF k=60) |
| `app/services/rag_service.py` | RAG interface: `search_normativo`, `search_historico`, `add_empresa_doc` |
| `app/services/doc_classifier.py` | LLM-based document classifier (13+ types) |
| `app/services/financial_statement_service.py` | Derives `FinancialStatement` rows from journal entries; exposes `list_financial_statements` |
| `app/services/nit_utils.py` | Colombian NIT normalization: `normalize_nit`, `normalize_optional_nit` |
| `app/models/database.py` | SQLAlchemy ORM models — includes `FinancialStatement` |
| `app/models/document_types.py` | `DocumentType` enum + pathway constants |
| `app/models/ingest_schemas.py` | Polymorphic extraction schemas per doc type |
| `app/api/v1/dashboard.py` | Dashboard endpoints: `/stats`, `/financial-summary`, `/monthly-trend` for real-time KPI and trend rendering |
| `data/` | Static seed data: 41 PUC accounts, 50 Estatuto Tributario articles, 16 Ley 43/1990 entries |

### Tech Stack

- **LLM:** Google Gemini 2.5 Flash (`langchain-google-genai`)
- **Orchestration:** LangGraph StateGraph
- **Embeddings:** BAAI/bge-m3 (1024 dims) via HuggingFace Inference API
- **Reranking:** BAAI/bge-reranker-v2-m3
- **Vector DB:** Supabase PostgreSQL + pgvector (HNSW + GIN for FTS)
- **ORM/Migrations:** SQLAlchemy 2.0 + Alembic

## Code Conventions

- **Imports:** stdlib → external → internal. Never wildcard imports.
- **Error handling:** Fail fast. Set `state["error"]` and return early — do not swallow exceptions.
- **State mutations:** All nodes receive and return `AgentState`. Never mutate shared objects outside the node.
- **DB sessions:** Always open with `SessionLocal()`, wrap in try/except/finally, close in `finally`. Prefer a single `db.commit()` per operation — avoid partial transactions.
- **Gemini calls:** Use `_gemini_with_retry_generic` for all extraction calls; it handles transient network errors.
- **NIT validation:** Colombian NITs must be cleaned (strip `.` and spaces) before storing. Reject empty strings.
- **PUC fallback:** When defaulting to account `519595`, emit an explicit `logger.warning`.

## After Every File Modification

Run these three steps in order before marking any task complete:

```bash
make lint        # ruff check — must show 0 errors
make format      # ruff format + black — auto-formats in place
make test        # pytest (e2e excluded) — must pass with no new failures
```

If `make lint` reports errors, fix them before continuing. Do not bypass with `# noqa` unless the error is a known intentional pattern (e.g. E402 after a sys.path manipulation block).

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
