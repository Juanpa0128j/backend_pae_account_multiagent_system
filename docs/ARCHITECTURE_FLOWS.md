# Architecture and Flow Diagrams

This document explains the current system architecture and runtime behavior using Mermaid diagrams.

## 1) System Architecture Overview

```mermaid
flowchart LR
    Client[Client / Frontend] --> API[FastAPI API Layer]

    subgraph API_Routes[API Routes]
      IngestRoute[/POST ingest upload/]
      ProcessRoute[/POST process accounting ingest_id/]
      StatusRoute[/GET process status process_id/]
      ResultRoute[/GET process result process_id/]
      SettingsRoute[/POST settings company nit setup/]
    end

    API --> API_Routes

    IngestRoute --> BGIngest[Background ingest runner]
    ProcessRoute --> BGProcess[Background process runner]

    BGIngest --> Graph[Unified LangGraph]
    BGProcess --> Graph

    subgraph GraphNodes[Graph Nodes]
      Supervisor[Supervisor FSM]
      Ingesta[Ingesta]
      Validate[Validate Output]
      Contador[Contador]
      Tributario[Tributario]
      Auditor[Auditor]
      Persist[DB Persist]
      Reportero[Reportero]
      ErrorTerminal[Error Terminal]
    end

    Graph --> GraphNodes

    SettingsRoute --> CompanySettings[(company_settings)]

    Persist --> IngestJobs[(ingest_jobs)]
    Persist --> TxPending[(transactions_pending)]
    Persist --> TxPosted[(transactions_posted)]
    Persist --> Journal[(journal_entry_lines)]
    Persist --> Audit[(audit_logs)]

    Tributario --> CompanySettings
    Tributario --> Reteica[(reteica_tarifas)]
    Tributario --> RAG[(vector_documents / RAG)]

    StatusRoute --> ProcessJobs[(process_jobs)]
    ResultRoute --> ProcessJobs
    ResultRoute --> TxPosted
```

Key idea:
- One unified graph handles multiple modes.
- API routes orchestrate background jobs and expose polling/result endpoints.

## 1.1) FSM State Catalog (Unified Graph)

The unified LangGraph finite-state machine has 9 explicit runtime states:

1. supervisor
  Purpose: central router and gatekeeper.
  Behavior: reads mode and current_agent, applies validation/routing rules, and decides the next state.

2. ingesta
  Purpose: document extraction and transaction interpretation.
  Behavior: parses source files and builds structured extracted data for ingest mode.

3. validate_output
  Purpose: schema validation and retry control for ingest output.
  Behavior: validates extracted payload, emits correction feedback, loops to ingesta on retryable errors, or advances to persistence.

4. contador
  Purpose: accounting classification.
  Behavior: classifies transactions into accounting entries and produces contador output for downstream tax/audit checks.

5. tributario
  Purpose: tax calculation and legal grounding.
  Behavior: computes retefuente, reteica, and IVA; enriches entries; loads company tax config and fails fast in process mode when required settings are missing.

6. auditor
  Purpose: accounting quality and compliance decision.
  Behavior: validates consistency and can approve, reject (loop back to contador), or fail the process.

7. db_persist
  Purpose: centralized persistence layer.
  Behavior: writes ingest/process outputs to DB (jobs, pending/posted transactions, journal lines, audit logs) and updates job statuses.

8. reportero
  Purpose: reporting mode terminal worker.
  Behavior: generates reporting outputs and exits without accounting persistence flow.

9. error_terminal
  Purpose: controlled failure sink.
  Behavior: ends execution when a non-recoverable error is detected upstream.

Note:
- LangGraph also has internal start/end markers, but they are framework boundary states rather than business runtime states.

## 2) Pipeline 1: Ingest Flow

```mermaid
flowchart TD
    A[Upload file] --> B[POST ingest upload]
    B --> C[Create ingest job]
    C --> D[Background ingest pipeline]

    D --> E[invoke_ingest_pipeline]
    E --> F[Supervisor mode ingest]
    F --> G[Ingesta]
    G --> H[Validate output]

    H -->|invalid and retry left| G
    H -->|invalid exhausted| X[End with validation error]
    H -->|valid| I[DB Persist]

    I --> J[Update ingest_jobs]
    I --> K[Create transactions_pending]
    I --> L[Write audit log]

    J --> M[Ingest complete]
    K --> M
    L --> M
```

Key idea:
- Ingest focuses on extraction and staging.
- Validation and retry happen before persistence.

## 3) Pipeline 2: Accounting Flow with Fail-Fast Preconditions

```mermaid
flowchart TD
    A[POST process accounting ingest_id] --> B{Preflight checks in API}

    B -->|No staged tx| E1[409 business_precondition NO_STAGED_TRANSACTIONS]
    B -->|Missing nit_receptor| E2[409 business_precondition MISSING_NIT_RECEPTOR]
    B -->|Missing company settings| E3[409 business_precondition MISSING_COMPANY_SETTINGS]
    B -->|All good| C[Create process_job and start background run]

    C --> D[invoke_accounting_pipeline]
    D --> S[Supervisor mode process]

    S --> C1[Contador]
    C1 --> S
    S --> T1[Tributario]
    T1 --> S
    S --> A1[Auditor]
    A1 --> S

    S -->|auditor rejects| C1
    S -->|approved| P[DB Persist]

    P --> W1[Update posted transactions]
    P --> W2[Create journal lines]
    P --> W3[Update process status completed]

    T1 -. reads .-> CS[(company_settings)]
    T1 -. reads .-> RT[(reteica_tarifas)]
    T1 -. may query .-> RAG[(RAG normativo)]
```

Key idea:
- Accounting processing is guarded by business preconditions.
- Tributario no longer silently defaults in process mode when required company settings are missing.

## 4) Process Status and Result Error Contract

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running
    running --> completed
    running --> failed

    state failed {
      [*] --> business_precondition
      [*] --> system_error
    }

    note right of queued
      status endpoint returns:
      process_id, status, stage, progress
    end note

    note right of failed
      status endpoint adds:
      error_category
      error_code
      remediation

      result endpoint:
      409 for business_precondition
      500 for system_error
    end note

    note right of completed
      result endpoint returns
      final posted transactions
      and journal payload
    end note
```

Key idea:
- Failed jobs are now typed for clients.
- Consumers can automate handling with category plus code plus remediation.

## Suggested Presentation Sequence

1. Start with architecture overview.
2. Show ingest flow first.
3. Show accounting flow and fail-fast points.
4. End with error contract to explain API behavior for frontend and integrations.
