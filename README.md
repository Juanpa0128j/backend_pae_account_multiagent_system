# Backend PAE Account Multiagent System

Backend for the PAE Account Multiagent System — a FastAPI application implementing a
multi-agent pipeline for automated Colombian accounting and tax compliance, backed by
a **Supabase pgvector RAG layer** for regulatory document retrieval.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [RAG System](#rag-system)
5. [Setup](#setup)
6. [Environment Variables](#environment-variables)
7. [Running Tests](#running-tests)
8. [Database Migrations](#database-migrations)
9. [Key Dependencies](#key-dependencies)
10. [Design Principles](#design-principles)

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
├── scripts/
│   └── populate_rag.py         # Seeds Supabase with all normativa documents
└── tests/
    ├── test_rag.py             # Core vector DB + RAG service tests (33 tests)
    └── test_rag_expanded.py    # Hybrid search + data integrity tests (35 tests)
```

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

1. Open this folder in VS Code.
2. Click **"Reopen in Container"** when prompted.
3. The container installs all dependencies automatically.

### Option 2: Local Setup

```bash
# 1. Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install Python dependencies
UV_LINK_MODE=copy uv sync

# 3. Copy and fill in environment variables
cp .env.example .env
# Edit .env: set DATABASE_URL, HUGGINGFACE_API_KEY, GEMINI_API_KEY

# 4. Apply database migrations
source .venv/bin/activate
alembic upgrade head

# 5. Seed the normativa vector collection
python scripts/populate_rag.py

# 6. Start the development server
uvicorn main:app --reload
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | Supabase PostgreSQL connection string (with `+asyncpg` or `+psycopg2`) |
| `HUGGINGFACE_API_KEY` | ✅ | HuggingFace Inference API key (for BGE-M3 embeddings + reranker) |
| `GEMINI_API_KEY` | ✅ | Google AI API key (for the LLM agent backbone) |
| `GEMINI_MODEL` | | Chat model name (default: `gemini-2.5-flash`) |
| `PORT` | | Server port (default: `8000`) |

Copy `.env.example` to `.env` and fill in the values. Never commit `.env` to version control.

---

## Running Tests

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Core RAG tests (33 tests — no API calls, runs in ~9 s):
pytest tests/test_rag.py -v

# Expanded hybrid search + data integrity tests (35 tests):
pytest tests/test_rag_expanded.py -v

# Full test suite:
pytest tests/ -v

# Run a single test class:
pytest tests/test_rag_expanded.py::TestDataFileIntegrity -v
```

All tests use an in-memory `FakeSupabaseVectorDB` / `ExtendedFakeDB` with deterministic
SHA-256-seeded embeddings. No HuggingFace API calls are made during testing.

---

## Database Migrations

Migrations are managed with **Alembic** against Supabase PostgreSQL.

```bash
# Apply all pending migrations:
alembic upgrade head

# Check current migration state:
alembic current

# Generate a new migration:
alembic revision --autogenerate -m "description"

# Rollback one step:
alembic downgrade -1
```

### Migration History

| Revision | Description |
|---|---|
| `8fb1b0855393` | Initial schema |
| `c3f8a2d91b5e` | `vector_documents` table + HNSW index + B-tree index on `collection_name` |
| `d4e5f6a7b8c9` | `content_tsv` GENERATED tsvector column + GIN index (enables hybrid search) |

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | REST API framework |
| `langgraph` | Multi-agent orchestration (StateGraph) |
| `langchain-google-genai` | Google Gemini LLM integration |
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

