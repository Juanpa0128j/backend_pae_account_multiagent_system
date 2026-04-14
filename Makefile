.PHONY: help install dev server test test-file test-class lint format format-check clean migrate migrate-new pipeline-test

# Default target
help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Setup"
	@echo "  install        Install all dependencies (uv sync)"
	@echo "  dev            Install dev dependencies"
	@echo ""
	@echo "Development"
	@echo "  server         Run development server with hot reload"
	@echo ""
	@echo "Testing"
	@echo "  pipeline-test  Run full pipeline with a real doc: make pipeline-test FILE=path/to/doc.pdf [NIT=123]"
	@echo "  test           Run all tests (excluding e2e)"
	@echo "  test-e2e       Run e2e tests (tests/e2e/ + supabase pipeline)"
	@echo "  test-file      Run a single test file: make test-file FILE=tests/test_foo.py"
	@echo "  test-class     Run a single test class: make test-class FILE=tests/test_foo.py CLASS=TestBar"
	@echo ""
	@echo "Lint & Format"
	@echo "  lint           Run ruff linter"
	@echo "  lint-fix       Auto-fix ruff lint errors"
	@echo "  format         Format code with ruff format + black"
	@echo "  format-check   Check formatting without writing"
	@echo ""
	@echo "Database"
	@echo "  migrate        Apply pending Alembic migrations"
	@echo "  migrate-new    Generate a new migration: make migrate-new MSG='description'"
	@echo ""
	@echo "Cleanup"
	@echo "  clean          Remove __pycache__, .pytest_cache, .ruff_cache, *.pyc"

# ── Setup ─────────────────────────────────────────────────────────────────────

install:
	uv sync
	uv run pre-commit install

dev:
	uv sync --group dev

# ── Development ───────────────────────────────────────────────────────────────

server:
	uv run uvicorn main:app --reload

# ── Testing ───────────────────────────────────────────────────────────────────

PYTEST_OPTS = --timeout=30 -v

test:
	uv run pytest tests/ $(PYTEST_OPTS) \
		--ignore=tests/e2e

test-e2e:
	uv run pytest tests/e2e/ tests/features/test_supabase_pipeline_feature.py $(PYTEST_OPTS)

test-file:
	@test -n "$(FILE)" || (echo "Usage: make test-file FILE=tests/test_foo.py" && exit 1)
	uv run pytest $(FILE) $(PYTEST_OPTS)

test-class:
	@test -n "$(FILE)" || (echo "Usage: make test-class FILE=tests/test_foo.py CLASS=TestBar" && exit 1)
	@test -n "$(CLASS)" || (echo "Usage: make test-class FILE=tests/test_foo.py CLASS=TestBar" && exit 1)
	uv run pytest $(FILE)::$(CLASS) $(PYTEST_OPTS)

# ── Pipeline smoke test ───────────────────────────────────────────────────────

BASE_URL ?= http://localhost:8000

pipeline-test:
	@test -n "$(FILE)" || (echo "Usage: make pipeline-test FILE=path/to/doc.pdf [NIT=123456]" && exit 1)
	@echo ">>> [1/4] Uploading $(FILE)..."
	$(eval INGEST_ID := $(shell \
		if [ -n "$(NIT)" ]; then \
			curl -sf -X POST $(BASE_URL)/api/v1/ingest/upload \
				-F "file=@$(FILE)" \
				-F "company_nit=$(NIT)" | python3 -c "import sys,json; d=sys.stdin.read(); print(json.loads(d)['ingest_id'])"; \
		else \
			curl -sf -X POST $(BASE_URL)/api/v1/ingest/upload \
				-F "file=@$(FILE)" | python3 -c "import sys,json; d=sys.stdin.read(); print(json.loads(d)['ingest_id'])"; \
		fi \
	))
	@test -n "$(INGEST_ID)" || (echo "ERROR: upload failed or server not running" && exit 1)
	@echo ">>> ingest_id=$(INGEST_ID)"
	@echo ">>> [2/4] Waiting for ingest to complete (polling every 5s)..."
	@for i in $$(seq 1 24); do \
		STATUS=$$(curl -sf $(BASE_URL)/api/v1/ingest/$(INGEST_ID) | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))"); \
		echo "    status=$$STATUS"; \
		if [ "$$STATUS" = "completed" ]; then break; fi; \
		if [ "$$STATUS" = "failed" ]; then echo "ERROR: ingest failed" && exit 1; fi; \
		sleep 5; \
	done
	@echo ">>> [3/4] Triggering accounting pipeline..."
	@curl -sf -X POST $(BASE_URL)/api/v1/process/accounting/$(INGEST_ID) | python3 -m json.tool
	@echo ">>> [4/4] Fetching result..."
	@sleep 10
	@curl -sf $(BASE_URL)/api/v1/process/accounting/$(INGEST_ID)/result | python3 -m json.tool

# ── Lint & Format ─────────────────────────────────────────────────────────────

lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

format:
	uv run ruff format .
	uv run black .

format-check:
	uv run ruff format --check .
	uv run black --check .

# ── Database ──────────────────────────────────────────────────────────────────

migrate:
	uv run alembic upgrade head

migrate-new:
	@test -n "$(MSG)" || (echo "Usage: make migrate-new MSG='description'" && exit 1)
	uv run alembic revision --autogenerate -m "$(MSG)"

# ── Cleanup ───────────────────────────────────────────────────────────────────

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
