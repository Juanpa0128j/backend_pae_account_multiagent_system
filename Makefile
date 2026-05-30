.PHONY: help install dev server test test-file test-class lint format format-check clean migrate migrate-new migrate-check-heads pipeline-test pipeline-setup-nit db-up db-down db-logs db-reset db-shell db-migrate seed dev-bootstrap inngest-install inngest-dev inngest-tunnel

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
	@echo "  migrate              Apply pending Alembic migrations to DATABASE_URL"
	@echo "  migrate-new          Generate a new migration: make migrate-new MSG='description'"
	@echo "  migrate-check-heads  Fail if alembic has more than one head (CI gate)"
	@echo ""
	@echo "Local Postgres+pgvector (docker-compose, port 5433)"
	@echo "  db-up          Start the local DB container"
	@echo "  db-down        Stop the local DB container (data preserved)"
	@echo "  db-reset       Stop, destroy volume, restart, run migrations"
	@echo "  db-migrate     Run alembic upgrade head against the local DB"
	@echo "  db-logs        Tail the local DB logs"
	@echo "  db-shell       Open a psql shell on the local DB"
	@echo "  seed           Seed PUC accounts + RAG normativa (takes 3-5 min)"
	@echo "  dev-bootstrap  One-shot: db-up + migrate + seed (run from devcontainer)"
	@echo ""
	@echo "Workflows (Inngest spike)"
	@echo "  inngest-dev    Run Inngest dev server pointed at the local backend"
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
	uv run python -m uvicorn main:app --reload

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
	$(eval PATHWAY := $(shell curl -sf $(BASE_URL)/api/v1/ingest/$(INGEST_ID) | python3 -c "import sys,json; d=sys.stdin.read(); print(json.loads(d).get('pathway',''))"))
	@if [ "$(PATHWAY)" = "work_with_existing" ]; then \
		echo ">>> [3/4] Via B doc (financial statement) — skipping accounting pipeline."; \
		echo ">>> [4/4] Done. Ingest result:"; \
		curl -sf $(BASE_URL)/api/v1/ingest/$(INGEST_ID) | python3 -m json.tool; \
	else \
		echo ">>> [3/4] Triggering accounting pipeline..."; \
		PROCESS_ID=$$(curl -sf -X POST $(BASE_URL)/api/v1/process/accounting/$(INGEST_ID) | python3 -c "import sys,json; d=sys.stdin.read(); print(json.loads(d)['process_id'])"); \
		echo "    process_id=$$PROCESS_ID"; \
		echo ">>> [4/4] Polling for result (up to 2min)..."; \
		for i in $$(seq 1 24); do \
			HTTP_CODE=$$(curl -s -o /tmp/pae_result.json -w "%{http_code}" $(BASE_URL)/api/v1/process/result/$$PROCESS_ID); \
			if [ "$$HTTP_CODE" = "200" ]; then python3 -m json.tool /tmp/pae_result.json; break; fi; \
			echo "    waiting... ($$i/24)"; \
			sleep 5; \
		done; \
	fi

pipeline-setup-nit:
	@test -n "$(NIT)" || (echo "Usage: make pipeline-setup-nit NIT=123456789 [CIUDAD=Bogotá] [CIIU=4719] [IVA=true]" && exit 1)
	@echo ">>> Seeding company settings for NIT=$(NIT)..."
	@curl -sf -X POST $(BASE_URL)/api/v1/settings/company/$(NIT)/setup \
		-H "Content-Type: application/json" \
		-d '{"nombre":"Empresa Test","ciudad":"$(or $(CIUDAD),Bogotá)","codigo_ciiu":"$(or $(CIIU),4719)","iva_responsable":$(or $(IVA),true)}' \
		| python3 -m json.tool
	@echo ">>> Company NIT=$(NIT) ready."

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

# Fails when two branches each generated a head — forces a rebase before
# the migrations can be merged. Wired into CI to catch concurrent edits
# from parallel PRs before they reach prod.
migrate-check-heads:
	@HEADS=$$(uv run alembic heads 2>/dev/null | grep -c '^.' || true); \
		if [ "$$HEADS" != "1" ]; then \
			echo "ERROR: alembic has $$HEADS heads (expected 1). Concurrent migrations detected — rebase + merge required."; \
			uv run alembic heads; \
			exit 1; \
		fi; \
		echo "OK: single migration head."

# ── Local Postgres (docker-compose.dev.yml) ───────────────────────────────────

LOCAL_DATABASE_URL ?= postgresql://pae:pae@localhost:5433/pae

db-up:
	docker compose -f docker-compose.dev.yml up -d
	@echo ">>> Waiting for Postgres to be ready..."
	@for i in $$(seq 1 20); do \
		docker compose -f docker-compose.dev.yml exec -T db pg_isready -U pae -d pae >/dev/null 2>&1 && break; \
		sleep 1; \
	done
	@echo ">>> Local DB ready at $(LOCAL_DATABASE_URL)"

db-down:
	docker compose -f docker-compose.dev.yml down

db-reset:
	docker compose -f docker-compose.dev.yml down -v
	$(MAKE) db-up
	$(MAKE) db-migrate

db-migrate:
	DATABASE_URL=$(LOCAL_DATABASE_URL) uv run alembic upgrade head

seed:
	uv run python scripts/seed_puc.py
	uv run python scripts/populate_rag.py

dev-bootstrap: db-up db-migrate seed
	@echo ">>> Dev environment ready (DB up + migrations applied + PUC/RAG seeded)."

db-logs:
	docker compose -f docker-compose.dev.yml logs -f db

db-shell:
	docker compose -f docker-compose.dev.yml exec db psql -U pae -d pae

# ── Workflows (Inngest spike) ─────────────────────────────────────────────────

INNGEST_BACKEND_URL ?= http://localhost:8000/api/inngest

INNGEST_VERSION ?= 1.19.4
INNGEST_ARCH ?= linux_amd64

inngest-install:
	@echo ">>> Installing Inngest CLI v$(INNGEST_VERSION) ($(INNGEST_ARCH)) to ./bin/inngest"
	@mkdir -p bin
	@curl -sfL -o /tmp/inngest.tar.gz \
		https://github.com/inngest/inngest/releases/download/v$(INNGEST_VERSION)/inngest_$(INNGEST_VERSION)_$(INNGEST_ARCH).tar.gz
	@tar -xzf /tmp/inngest.tar.gz -C bin inngest
	@rm /tmp/inngest.tar.gz
	@chmod +x bin/inngest
	@./bin/inngest --version

inngest-dev:
	@if [ ! -x ./bin/inngest ]; then \
		echo "Inngest CLI missing. Run: make inngest-install" && exit 1; \
	fi
	@echo ">>> Inngest dev server. Backend must be running at http://localhost:8000."
	@echo ">>> Set WORKFLOW_ENGINE=inngest and INNGEST_DEV=1 in .env first."
	@echo ">>> UI: http://localhost:8288"
	./bin/inngest dev -u $(INNGEST_BACKEND_URL)

# Expose the local backend to Inngest Cloud via ngrok. Requires ngrok installed
# and authenticated (`ngrok config add-authtoken ...`). Register the printed
# https URL in the Inngest dashboard for the branch env (Apps → Sync URL).
NGROK_PORT ?= 8000

inngest-tunnel:
	@command -v ngrok >/dev/null 2>&1 || { echo "ngrok not installed. See docs/operations/inngest-cloud.md"; exit 1; }
	@echo ">>> Opening ngrok tunnel to localhost:$(NGROK_PORT)"
	ngrok http $(NGROK_PORT)

# ── Cleanup ───────────────────────────────────────────────────────────────────

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
