# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Context.

**PAE — Proyecto de Aplicación Específica (Universidad Nacional de Colombia)**

**Title:** Plataforma de asistencia contable autónoma para el manejo de cuentas y generación de informes contables

**Goal:** An LLM-driven agentic system that automates the full Colombian accounting cycle with precision comparable to a human accountant. Quality is validated via an agentic evaluator and, where possible, by a human CPA.

**Team:** Juan Pablo Mejía, Mateo Builes, Samuel Castaño, Jhon Edison Pinto

**Regulatory frame:** Estatuto Tributario colombiano (DIAN), Ley 43/1990, Ley 1314/2009, NIIF Plenas (Decreto 2420/2015), PUC colombiano.

## Commands

**Package manager:** `uv` — never use pip directly.

```bash
# First-time setup (host shell first, then devcontainer)
# 1) Host: make db-up                  # starts pgvector container on Mac
# 2) Reopen in devcontainer
# 3) Inside devcontainer:
make dev-bootstrap                    # db-up + alembic upgrade + seed_puc + populate_rag
# Or manually:
uv sync
uv run alembic upgrade head
uv run python scripts/seed_puc.py        # 84 PUC accounts into Postgres
uv run python scripts/populate_rag.py    # 107 normativa docs into pgvector (3-5 min)
uv run python scripts/populate_rag.py --force  # Force re-index

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
make migrate-check-heads                             # CI gate: fails if alembic has >1 head

# Local Postgres+pgvector (docker-compose.dev.yml, port 5433)
make db-up                                           # start local DB
make db-migrate                                      # alembic upgrade head against local
make db-reset                                        # destroy volume + remigrate
make db-shell                                        # psql shell
```

## Database environments

The same Alembic schema runs against three databases:

- **Local (dev):** docker-compose pgvector container — start with `make db-up`.
  - **Host uvicorn**: `DATABASE_URL=postgresql://pae:pae@localhost:5433/pae` in `.env`.
  - **Inside devcontainer**: `DATABASE_URL=postgresql://pae:pae@host.docker.internal:5433/pae` (Docker Desktop Mac/Win) or `@172.17.0.1:5433` (Linux native).
  - First-time order: (1) host `make db-up`, (2) reopen in devcontainer, (3) `make dev-bootstrap` (or `uv run alembic upgrade head` + `uv run python scripts/seed_puc.py` + `uv run python scripts/populate_rag.py`).
  - Forgetting alembic upgrade → backend crashes `relation "company_settings" does not exist`.
  - Forgetting seed → contador falls back to 5195 (no PUC table to query).
  - Forgetting populate_rag → RAG retrieval empty, lower classification quality.
- **CI (pull requests):** ephemeral `pgvector/pgvector:pg16` service in
  GitHub Actions; migrations run fresh per workflow.
- **Production:** Supabase Postgres; only merged-to-main migrations touch it.

When two PRs each add a migration on top of the same parent, alembic ends
up with multiple heads. CI runs `make migrate-check-heads` before tests
and fails until the second author rebases their migration on top of the
merged one. Never merge a PR with multiple heads.

### Configurable national tax rates (`national_rates` table)

Migration `b8c9d0e1f2a3` adds `national_rates` (code PK, value, descripcion, norma_referencia, vigente_desde). Seeded with 4 statutory rates:
- `retefuente_servicios` 4% (Art. 392 ET)
- `retefuente_bienes` 2.5% (Art. 401 ET)
- `retefuente_arrendamiento` 3.5% (Art. 401 ET)
- `renta_general` 35% (Art. 240 ET, L.2277/2022)

These replace the module-level constants in `settings.py:32-36, 214`. The `/setup` endpoint will read from this table (Phase 4) so rate changes require no code deploy. DB layer: `list_national_rates`, `get_national_rate`, `upsert_national_rate` in `db_service.py`. API endpoints at `/api/v1/settings/national-rates` (Phase 3, pending).

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

### Durable Workflow Layer (Inngest)

Feature-flagged via `WORKFLOW_ENGINE=inngest` (default: `inline`). When enabled, both pipelines run as Inngest functions instead of FastAPI `BackgroundTasks`. LangGraph pipelines stay intact — Inngest wraps each as a single outer step.

Key behaviors when `WORKFLOW_ENGINE=inngest`:
- **Per-NIT concurrency**: `Concurrency(limit=5, key="event.data.company_nit")` — prevents one tenant from starving others
- **OpenAI throttle**: `Throttle(limit=400/60s, key="openai")` — cluster-wide rate budget
- **Singleton guard**: `Singleton(key="event.data.process_id", mode="skip")` — deduplicates double-dispatch
- **HITL durability**: `ctx.step.wait_for_event("app/process.audit-confirmed", timeout=1h)` — audit-confirmation gate survives backend restarts; times out with Spanish error copy
- **Bulk ingest fan-out**: When `multi_file_mode="documents"` + N>1 files, each file gets its own `IngestJob` row and `app/ingest.start` event. Failure of one file marks only that job FAILED; others continue.
- **LangSmith bridge**: `app/workflows/langsmith_bridge.py` injects `inngest_run_id`, `inngest_event_id`, `inngest_fn_id` into LangSmith trace metadata for cross-system correlation.

Relevant modules:
- `app/workflows/inngest_client.py` — singleton client; `INNGEST_IS_PRODUCTION` decouples signature verification from `APP_ENV`
- `app/workflows/dispatch.py` — `dispatch_process_start`, `dispatch_ingest_start`
- `app/workflows/functions/process_pipeline.py` — process Inngest function
- `app/workflows/functions/ingest_pipeline.py` — ingest Inngest function
- `app/workflows/langsmith_bridge.py` — LangSmith context manager

Local testing with Inngest Cloud: set `INNGEST_DEV=false`, `INNGEST_IS_PRODUCTION=true`, run `make inngest-tunnel` (ngrok). See `docs/operations/inngest-cloud.md`.

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
- **PDF Report generation:** ReportLab; account descriptions are word-wrapped in Paragraph objects to prevent text overflow in table cells (common with long Colombian account names like "RETENCION DE IVA POR VENTA DE SERVICIOS").

### Document Types

The system handles 13+ Colombian financial document types (facturas, extractos bancarios, declaraciones de IVA, retención en la fuente, etc.). Document classification is in `app/services/doc_classifier.py` and `app/models/document_types.py`.

**Recent: recibo_caja improvements (VIA A)** — `recibo_caja` (cash receipt) now captures `tipo_recibo` (cobro_cartera | venta_directa | otro) and `referencia_factura` to enable intelligent accounting classification:
- **Extraction:** Enhanced prompt explicitly requests classification signals.
- **Mapping:** Dedicated handler in `document_mappers.py` fixes nit_emisor extraction (was reading from emisor instead of recibido_de).
- **Accounting:** Contador rule now uses `tipo_recibo` to intelligently choose 130505 (cuentas por cobrar) vs 4xxx (ingresos) for credit side. See `app/core/prompts/contador.py` for rule details.

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
- **`balance_general_anterior` remapping:** This Vía B doc type exists only in the UI/ingest layer. `persist_node.py` remaps it to `statement_type='balance_general'` before DB insert. `_load_prior_balance` in `financial_statement_service.py` finds the prior-period balance via `period_end < period_start`. Do not store it under a different statement_type. The 4 Vía B slots that accept uploads are: `balance_general`, `balance_general_anterior`, `estado_resultados`, `libro_auxiliar`. If adding a new Vía B doc type, update: `document_types.py` (enum + `PATHWAY_MAP` + `_VIA_B_TYPES`), `supervisor.py` (hardcoded `via_b_doc_types` set), `ingest_agent.py` (`_EXTRACT_METHOD_MAP` + `_VIA_B_STATEMENT_TYPES`), and `persist_node.py` if a remap is needed.
- **UVT base mínima retención (2026):** `tributario_agent.py` zeroes retefuente / reteICA below the UVT-derived monthly minimum: servicios=4 UVT, bienes/arrendamiento=27 UVT, reteICA=4 UVT (UVT_2026=$52,374). Constants: `BASE_MINIMA_RETEFUENTE_UVT`, `BASE_MINIMA_RETEICA_UVT`. Bump these tables every January when DIAN publishes the new UVT.
- **`aplicar_retencion=false` flag:** Source docs that text-state "no aplicar retención según artículo X ET" (e.g. cuenta_cobro) emit `informacion_adicional.aplicar_retencion=false` via the ingest prompt. Tributario reads it in `_extract_source_taxes` and forces retefuente=0 and reteICA=0 regardless of base. Honor this flag for any doc_type that surfaces it.
- **Cuenta de cobro (cuenta_cobro):** Always `totales.total_iva=0` — natural persons issuing CC are not IVA-responsible by definition. Mapper branch in `document_mappers.py` reads `prestador.nit/cedula` as `nit_emisor`. Contador `_DOC_GUIDANCE["cuenta_cobro"]` maps expense by concept: honorarios/asesoría/outsourcing → 511505, comisiones → 511510, servicios técnicos → 511525, arrendamientos → 511525/5140. Account 5305 (Gastos Financieros) is forbidden.
- **Multi-transaction contador loop:** When `len(raw_transactions) > 1` (bank statements, recibo_pago_impuesto with conceptos, conciliacion_bancaria, …), `contador_agent.py` iterates and calls `extract_contador_output` once per movement. Single-tx docs keep the single-call path. Without the loop the LLM lazily returns one asiento pair and persist clones it across every pending tx.
- **Parser cache key includes `parser_mode`:** LlamaParse results cached in shared Postgres `parse_cache` table with composite key (content_sha256, parser_mode), 7-day TTL swept on write. See `app/services/parse_cache_service.py`. Switching fast↔premium↔gpt4o invalidates the cache. Best-effort by contract — cache failure never breaks ingest.
- **Bank statements need `parser_mode=premium`:** `fast` and `gpt4o` modes fabricate spurious rows and invert debit/credit signs on multi-column statements. Premium reconciles cleanly against `total_abonos + total_cargos`. Document this in any new doc_type guidance involving tabular layouts.

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
