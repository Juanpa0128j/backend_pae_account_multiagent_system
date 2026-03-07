# Backend PAE Account Multiagent System (Modular Monolith)

This is the backend for the PAE Account Multiagent System, built with FastAPI and designed following a Modular Monolith architecture. It implements a **Supervisor FSM** that routes three pipelines (ingest / process / reporting) for automated Colombian accounting, with a **Vector DB + RAG layer** for normativa retrieval and a structured `agent_log` execution trace at every node.

## Project Structure

```text
├── app/
│   ├── api/v1/       # Versioned REST endpoints (FastAPI routers)
│   ├── agents/       # Multi-agent logic (LangGraph, Supervisor-Worker)
│   ├── models/       # Pydantic schemas and API contracts
│   ├── core/         # Configuration, Gemini client, ChromaDB vector store
│   ├── services/     # Business logic, RAG service, PDF processing
│   └── main.py       # Application entry point
├── data/             # Seed data (PUC accounts, normativa tributaria)
├── docs/             # Architecture docs and implementation status
├── scripts/          # Utility scripts (populate_rag.py, demo_supabase_process.py)
├── tests/            # Unit and integration tests
├── .env.example      # Template for environment variables
└── pyproject.toml    # Dependency management (uv)
```

### Folder Purposes & Contribution Guidelines

| Folder | Purpose | How to Contribute |
| :--- | :--- | :--- |
| `app/api/` | Defines the REST interface. | Add new versioned routers (e.g., `v1`, `v2`) and link them in `main.py`. Keep logic minimal; delegate to agents or services. |
| `app/agents/` | Orchestrates LLM-based agents via the Supervisor FSM. Each node emits structured `LogEntry` events to `agent_log`. | Implement new worker nodes or refine the supervisor logic using LangGraph. Update the shared `AgentState` in `state.py`. |
| `app/models/` | Houses the data contracts. | Define Pydantic models for request/response validation. Always update these before changing API implementations to maintain the contract. |
| `app/core/` | Cross-cutting concerns. | Configuration (`config.py`), Gemini client (`gemini_client.py`), ChromaDB vector store (`vectordb.py`). |
| `app/services/` | Deterministic logic + RAG. | `rag_service.py` for semantic search, `pdf_processor.py` for text extraction, `validation_engine.py` for schema compliance. |
| `data/` | Static seed data. | PUC accounts and Estatuto Tributario articles used to populate the normativa vector collection. |
| `scripts/` | CLI utilities. | `populate_rag.py` seeds the ChromaDB normativa collection. `demo_supabase_process.py` runs an end-to-end demo. |

## Design Principles
1. Establish in advance which contracts we are going to use to communicate between the backend and frontend.
2. Use devcontainers, linters, formatters, and LSP for development.
3. Use different branches to develop features or make specific fixes, then open a PR to merge into main. Ideally, we should all be able to review everyone's PRs.
4. Use .env and add it to .gitignore.
5. Add comments when necessary and give variables meaningful names.
6. CAREFULLY REVIEW WHAT THE AI DOES.
7. Write short and clear documentation.
8. Simplify and divide.

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
  │                                           ├─[approved]─→ db_persist → END
  │                                           └─[rejected]─→ contador (with feedback)
  └─[mode=reporting]─────────────────────→ reportero → END
```

### Pipeline 1 — Ingest (`mode="ingest"`)
PDF upload → LlamaParse extraction → Gemini interpretation → schema validation (max 3 retries) → database persistence.

### Pipeline 2 — Process (`mode="process"`)
Staged transactions → Contador (PUC classification) → Tributario (tax calc) → Auditor (double-entry review) → feedback loop or persistence. **Tributario, Auditor, and Reportero are stubs** pending Phase 3 (Sprints 10–15).

### Pipeline 3 — Reporting (`mode="reporting"`)
Generates balance / P&L reports via the Reportero agent (stub, Phase 3).

> See `docs/IMPLEMENTATION_STATUS.md` for the full Phase 3 roadmap.

## RAG (Retrieval-Augmented Generation)

The system uses **ChromaDB** as an embedded vector database and **Gemini Embeddings** (`models/gemini-embedding-001`) via `langchain-google-genai` for semantic search.

### Architecture

```text
Agent (supervisor / ingesta / etc.)
    │
    ▼
RAGService                    ← clean Pydantic interface
    │
    ▼
ChromaVectorDB (singleton)    ← embeddings via Gemini
    │
    ▼
ChromaDB PersistentClient     (./storage/chromadb/)
    ├── normativa_colombia_v1   (56 docs: PUC + ET, read-only)
    └── empresa_{nit}_docs      (per-company, read/write)
```

### Collections

| Collection | Content | Access |
|---|---|---|
| `normativa_colombia_v1` | 41 PUC accounts (1105–6205) + 15 Estatuto Tributario articles | Read-only (seeded by script) |
| `empresa_{nit}_docs` | Company-specific invoices, receipts, and documents | Read/Write (per NIT) |

### RAG Service Methods

| Method | Default Results | Purpose |
|---|---|---|
| `search_normativo(query)` | 5 | Search PUC + ET for regulatory context |
| `search_historico(nit, query)` | 3 | Search company documents for duplicates / history |
| `add_empresa_doc(nit, text)` | — | Store new company document |

### Seeding the Normativa Collection

```bash
# First time (or after data changes):
python scripts/populate_rag.py

# Force re-index:
python scripts/populate_rag.py --force
```

### Checking RAG Status

```bash
curl http://localhost:8000/api/v1/evaluation/rag-status
```

## Setup

### Option 1: Devcontainer (Recommended)
1. Open this folder in VS Code.
2. Click "Reopen in Container" when prompted.
3. Dependencies will be installed automatically.

### Option 2: Local Setup
1. Install `uv`.
2. Run `uv sync` to install dependencies.
3. Copy `.env.example` to `.env` and fill in the values.
4. Seed the normativa collection: `python scripts/populate_rag.py`
5. Run the server: `uv run uvicorn main:app --reload`

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` | Google AI API key (required) | — |
| `GEMINI_MODEL` | Chat model | `gemini-2.5-flash` |
| `GEMINI_EMBEDDING_MODEL` | Embedding model | `models/gemini-embedding-001` |
| `LLAMA_CLOUD_API_KEY` | LlamaCloud API key for PDF parsing (required) | — |
| `CHROMA_PERSIST_PATH` | ChromaDB storage directory | `./storage/chromadb` |
| `DATABASE_URL` | Database connection string | `sqlite:///./storage/pae.db` |
| `PORT` | Server port | `8000` |

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

## Running Tests

```bash
# Full suite:
pytest tests/ -v

# Individual test files:
pytest tests/test_e2e_phase2.py -v        # 17 tests — Supervisor FSM, all pipelines, retry, error_terminal
pytest tests/test_validation_system.py -v  # 40 tests — Schema validation engine
pytest tests/test_agent_integration.py -v  # ~15 tests — Agent pipeline integration
pytest tests/test_database.py -v           # ~20 tests — ORM + db_service CRUD
pytest tests/test_rag.py -v                # 29 tests  — Vector DB + RAG service
```

## Key Dependencies

| Package | Version | Purpose |
|---|---|---|
| `langchain` | ≥1.0.0 | LLM application framework |
| `langgraph` | ≥1.0.0 | Multi-agent orchestration |
| `langchain-google-genai` | ≥4.0.0 | Gemini chat + embeddings |
| `langchain-community` | ≥0.4.0 | ChromaDB LangChain integration |
| `chromadb` | ≥1.0.0 | Embedded vector database |
| `llama-parse` | ≥0.6.0 | PDF parsing via LlamaCloud |
| `pydantic-settings` | ≥2.1.0 | Centralized env configuration |
