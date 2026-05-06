# scripts/dev — Developer-Only Scripts

These scripts are NOT part of the production code path. They contain synthetic
data, fake LLM clients, or simulated pipelines used for local development,
integration debugging, or demos.

Do not invoke from CI, production startup, or the FastAPI app.

## Contents

- `demo_supabase_process.py` — End-to-end pipeline demo with `FakeLlamaParse`
  and `FakeGeminiClient` that produce deterministic canned output. Useful for
  reproducing pipeline bugs without consuming LLM/parser quota.
- `simulate_frontend_full_pipeline.py` — Generates synthetic Colombian
  accounting documents and runs them through the full pipeline. Used to
  validate end-to-end behavior without real document uploads.
- `demo_reportero_reports.py` — Reportero agent demo.
- `check_provider_usage.py` — LLM provider usage inspector.
- `inspect_nit_data.py` — NIT data inspection helper.
