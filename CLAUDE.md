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
2. **Procesamiento:** Pending transactions → Contador (PUC classification) → Tributario (IVA/retención) → Auditor (double-entry) → feedback loop or persist
3. **Reportería:** Balance sheets, P&L, cash flow via Reportero agent

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
