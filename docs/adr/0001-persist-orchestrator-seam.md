# Split Persist Node into Proceso Contable Seam

The original `app/agents/persist_node.py` (1,484 lines) handled Via A transaction persistence, Via B financial statement persistence, journal entry construction, duplicate detection, process job updates, financial statement auto-derivation, and pre-persist auditing — all behind a single `db_persist_node(state)` entry point. It was the shallowest critical module in the system: enormous interface, minimal abstraction, and zero unit tests because the behavior was too tightly coupled to the LangGraph state object and global `SessionLocal`.

We decided to split it into three deep modules:

- `JournalBuilder` — pure function: pending transactions + contador output → journal entries
- `StatementDeriver` — pure function: journal entries → financial statement rows
- `PersistOrchestrator` — thin adapter that opens a DB session, calls the two builders, and commits

The pure builders are fully unit-testable without mocking `SessionLocal` or SQLAlchemy. The orchestrator is testable with an in-memory SQLite session. This concentrates persistence logic behind a small interface while keeping the state-mutation surface minimal.
