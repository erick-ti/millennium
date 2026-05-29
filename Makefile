.PHONY: help install dev test lint format typecheck migrate migrate-up shell superuser up down logs ps build clean frontend-install frontend-lint frontend-build frontend-snapshot-schema frontend-gen-api

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

BACKEND := cd backend &&
FRONTEND := cd frontend &&
COMPOSE := docker compose -f infra/docker-compose.yml
DOCKER_RUN := $(COMPOSE) run --rm backend

help:
	@echo "Common targets:"
	@echo "  install            uv sync (creates backend/.venv)"
	@echo "  dev                Start docker-compose stack (postgres, redis, web, workers, frontend)"
	@echo "  test               Run pytest locally"
	@echo "  lint               Run ruff check + mypy"
	@echo "  format             Run ruff format + ruff --fix"
	@echo "  typecheck          Run mypy"
	@echo "  migrate            Create new Django migrations (in container)"
	@echo "  migrate-up         Apply Django migrations (in container)"
	@echo "  shell              Django shell (in container)"
	@echo "  superuser          Create Django superuser (in container)"
	@echo "  up / down          docker compose up -d / down"
	@echo "  logs               docker compose logs -f"
	@echo "  ps                 docker compose ps"
	@echo "  build              Rebuild backend image"
	@echo "  clean              Remove caches and .venv"
	@echo "  frontend-install        npm ci in frontend/"
	@echo "  frontend-lint           Run frontend eslint"
	@echo "  frontend-build          Run next build (type-check + bundle)"
	@echo "  frontend-snapshot-schema  Snapshot OpenAPI schema to frontend/openapi.json"
	@echo "  frontend-gen-api        Regenerate the TypeScript API client from openapi.json"

install:
	$(BACKEND) uv sync --group dev

dev: up
	@echo "→ http://localhost:3000/                (Next.js frontend)"
	@echo "→ http://localhost:8000/api/docs/        (Swagger UI)"
	@echo "→ http://localhost:8000/api/health/"
	@echo "→ http://localhost:8000/admin/"

test:
	$(BACKEND) uv run pytest

lint:
	$(BACKEND) uv run ruff check .
	$(BACKEND) uv run mypy .

format:
	$(BACKEND) uv run ruff format .
	$(BACKEND) uv run ruff check --fix .

typecheck:
	$(BACKEND) uv run mypy .

migrate:
	$(DOCKER_RUN) python manage.py makemigrations

migrate-up:
	$(DOCKER_RUN) python manage.py migrate

shell:
	$(DOCKER_RUN) python manage.py shell

superuser:
	$(DOCKER_RUN) python manage.py createsuperuser

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

build:
	$(COMPOSE) build

clean:
	$(BACKEND) rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov

# Frontend ergonomics (CI runs the same commands in .github/workflows/frontend.yml).
frontend-install:
	$(FRONTEND) npm ci

frontend-lint:
	$(FRONTEND) npm run lint

frontend-build:
	$(FRONTEND) npm run build

# Snapshot the OpenAPI schema for the @hey-api/openapi-ts client generator.
# Uses the test settings (sqlite, no Redis) so it runs offline without the
# compose stack — drf-spectacular generates from code, not DB queries.
# --validate fails fast on schema warnings, the same gate test_schema.py
# enforces in CI (DECISIONS 2026-05-27 Phase 4 slice 2).
frontend-snapshot-schema:
	$(BACKEND) uv run python manage.py spectacular --settings config.settings.test --format openapi-json --validate --file ../frontend/openapi.json
	@echo "✓ Schema → frontend/openapi.json"

# Regenerate the typed TS client from the snapshot. Commits the output under
# frontend/src/lib/api/ so PR diffs show the API surface change (the schema
# acquisition decision — committed snapshot + committed generated client).
frontend-gen-api:
	$(FRONTEND) npm run gen:api
