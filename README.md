# Backend PAE Account Multiagent System (Modular Monolith)

This is the backend for the PAE Account Multiagent System, built with FastAPI and designed following a Modular Monolith architecture. It implements a multi-agent pipeline for automated Colombian accounting, with a **Vector DB + RAG layer** for normativa retrieval.

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
├── scripts/          # Utility scripts (populate_rag.py)
├── tests/            # Unit and integration tests
├── .env.example      # Template for environment variables
└── pyproject.toml    # Dependency management (uv)
```

### Folder Purposes & Contribution Guidelines

| Folder | Purpose | How to Contribute |
| :--- | :--- | :--- |
| `app/api/` | Defines the REST interface. | Add new versioned routers (e.g., `v1`, `v2`) and link them in `main.py`. Keep logic minimal; delegate to agents or services. |
| `app/agents/` | Orchestrates LLM-based agents. | Implement new worker nodes or refine the supervisor logic using LangGraph. Update the shared `AgentState` in `state.py`. |
| `app/models/` | Houses the data contracts. | Define Pydantic models for request/response validation. Always update these before changing API implementations to maintain the contract. |
| `app/core/` | Cross-cutting concerns. | Configuration (`config.py`), Gemini client (`gemini_client.py`), ChromaDB vector store (`vectordb.py`). |
| `app/services/` | Deterministic logic + RAG. | `rag_service.py` for semantic search, `pdf_processor.py` for text extraction, `validation_engine.py` for schema compliance. |
| `data/` | Static seed data. | PUC accounts and Estatuto Tributario articles used to populate the normativa vector collection. |
| `scripts/` | CLI utilities. | `populate_rag.py` seeds the ChromaDB normativa collection. |

## Design Principles
1. Establish in advance which contracts we are going to use to communicate between the backend and frontend.
2. Use devcontainers, linters, formatters, and LSP for development.
3. Use different branches to develop features or make specific fixes, then open a PR to merge into main. Ideally, we should all be able to review everyone's PRs.
4. Use .env and add it to .gitignore.
5. Add comments when necessary and give variables meaningful names.
6. CAREFULLY REVIEW WHAT THE AI DOES.
7. Write short and clear documentation.
8. Simplify and divide.

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
| `CHROMA_PERSIST_PATH` | ChromaDB storage directory | `./storage/chromadb` |
| `DATABASE_URL` | Database connection string | `sqlite:///./storage/pae.db` |
| `PORT` | Server port | `8000` |

## Running Tests

```bash
# RAG tests only (no API calls, 29 tests):
pytest tests/test_rag.py -v

# Full suite:
pytest tests/ -v
```

## Key Dependencies

| Package | Version | Purpose |
|---|---|---|
| `langchain` | ≥1.0.0 | LLM application framework |
| `langgraph` | ≥1.0.0 | Multi-agent orchestration |
| `langchain-google-genai` | ≥4.0.0 | Gemini chat + embeddings |
| `langchain-community` | ≥0.4.0 | ChromaDB LangChain integration |
| `chromadb` | ≥1.0.0 | Embedded vector database |
| `pydantic-settings` | ≥2.1.0 | Centralized env configuration |
