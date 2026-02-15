# Backend PAE Account Multiagent System (Modular Monolith)

This is the backend for the PAE Account Multiagent System, built with FastAPI and designed following a Modular Monolith architecture.

## Project Structure

```text
├── app/
│   ├── api/          # API Route definitions (FastAPI routers)
│   ├── agents/       # Multi-agent logic (LangGraph, Supervisor-Worker)
│   ├── models/       # Pydantic schemas and API contracts
│   ├── core/         # Global configuration, security (JWT), and utilities
│   ├── services/     # Shared business logic and external integrations
│   └── main.py       # Application entry point
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
| `app/core/` | Cross-cutting concerns. | Add global settings, security middleware, or logging configurations here. Avoid business logic. |
| `app/services/` | Deterministic logic. | Implement non-AI business logic, database CRUD operations, or third-party API clients. |

## Design Principles
1. Establish in advance which contracts we are going to use to communicate between the backend and frontend.
2. Use devcontainers, linters, formatters, and LSP for development.
3. Use different branches to develop features or make specific fixes, then open a PR to merge into main. Ideally, we should all be able to review everyone's PRs.
4. Use .env and add it to .gitignore.
5. Add comments when necessary and give variables meaningful names.
6. CAREFULLY REVIEW WHAT THE AI DOES.
7. Write short and clear documentation.
8. Simplify and divide.

## Setup

### Option 1: Devcontainer (Recommended)
1. Open this folder in VS Code.
2. Click "Reopen in Container" when prompted.
3. Dependencies will be installed automatically.

### Option 2: Local Setup
1. Install `uv`.
2. Run `uv sync` to install dependencies.
3. Copy `.env.example` to `.env` and fill in the values.
4. Run the server: `uv run uvicorn main:app --reload`.
