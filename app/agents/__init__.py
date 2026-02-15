"""
PILOT AGENT DEVELOPMENT INSTRUCTIONS:
------------------------------------
Follow these steps to implement the pilot agent and merge it with the infrastructure:

1. DEFINITION OF STATE:
   - Create `app/agents/state.py`.
   - Define a TypedDict `AgentState` for LangGraph to maintain context (e.g., list of documents, current processing status, identified errors).

2. SUPERVISOR IMPLEMENTATION:
   - Create `app/agents/supervisor.py`.
   - Implement the "Supervisor-Worker" pattern as per the architectural design.
   - Use a router function to decide the next step based on `AgentState`.

3. WORKER NODES (PROTOTYPES):
   - Implement `app/agents/ingesta_worker.py`: Focus on PDF text extraction (using `pypdf` or `pdfplumber`).
   - Implement `app/agents/contador_worker.py`: Stub for classification logic.

4. GRAPH ORCHESTRATION:
   - Create `app/agents/graph.py` to define the LangGraph `StateGraph`.
   - Compile the graph and export an `executor` function.

5. API INTEGRATION:
   - In `app/api/v1/ingest.py`, replace the simulated logic with a call to the compiled LangGraph executor.
   - Ensure the final output of the graph is validated against `app.models.schemas.IngestResponse`.

6. TESTING:
   - Create a test script in `tests/pilot_test.py` to run the graph end-to-end without the API layer first.
"""
