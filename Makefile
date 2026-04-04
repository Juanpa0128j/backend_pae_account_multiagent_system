.PHONY: help install dev server test test-file test-class lint format format-check clean migrate migrate-new

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
