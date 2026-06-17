# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    WEB_CONCURRENCY=2

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --group dev

COPY . .

# Deployed commit, baked at build time and surfaced by /api/status/ (the dashboard's
# "what's live"). deploy.sh passes --build-arg GIT_SHA=$(git rev-parse --short HEAD);
# CI/local builds pass nothing → "unknown" (base.py reads it with that safe default, so
# collectstatic below — which runs prod settings — never fails on it). Placed after
# `COPY . .` so a SHA change doesn't bust the deps layer beyond what the copy already does.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

# collectstatic for prod (manifest storage). Inline placeholder env vars satisfy
# prod settings' fail-closed validators; none of these reach a network call.
RUN DJANGO_SETTINGS_MODULE=config.settings.prod \
    DJANGO_SECRET_KEY=collectstatic-build-only-not-real \
    DJANGO_ALLOWED_HOSTS=build \
    DJANGO_CSRF_TRUSTED_ORIGINS=https://build \
    DATABASE_URL=postgres://build:build@build:5432/build \
    CELERY_BROKER_URL=redis://build:6379/1 \
    CELERY_RESULT_BACKEND=redis://build:6379/2 \
    python manage.py collectstatic --noinput

RUN useradd --create-home --shell /bin/bash --uid 1000 app \
    && chown -R app:app /app /opt/venv
USER app

EXPOSE 8000

# Shell-form + `exec` on purpose: shell form so ${PORT:-8000} expands (Railway
# injects PORT and routes the edge + healthcheck to it); `exec` guarantees
# gunicorn replaces the shell as PID 1 and receives SIGTERM directly no matter
# how the command grows (dash — this image's /bin/sh — happens to tail-exec a
# sole simple command anyway, but that vanishes the moment the CMD gains `&&`
# or a trap, and a resident sh does NOT forward signals: every deploy/stop
# would hang to the 10s SIGKILL instead of shutting down gracefully).
# `[::]` binds dual-stack: Railway private networking resolves
# *.railway.internal to IPv6 (legacy environments) or IPv4+IPv6 (environments
# created after 2025-10-16), while IPv4 healthchecks/compose connect via
# IPv4-mapped addresses. Workers come from WEB_CONCURRENCY (above), which
# gunicorn reads natively.
# --timeout 120 AND --graceful-timeout 120 are sized together to the
# synchronous CSV import (POST /api/imports/batches/ runs run_import inline):
# gunicorn's 30s defaults would kill a long import mid-request — steady-state
# via the heartbeat timeout (SIGABRT escalating to SIGKILL; the sync worker
# only notifies between requests, so --timeout is a hard per-request cap) and
# deploy-time via the SIGTERM grace window — leaving a stuck PROCESSING batch
# (recoverable via re-import dedup, but pointlessly). MEASURED 2026-06-12: a
# worst-case all-materialize import runs ~4.4ms/row on local Postgres, so the
# MAX_UPLOAD_ROWS=10k cap (apps/imports/views.py — sized to THIS timeout in
# the same review) ≈ 44s local / est. ≤100s over Railway private networking.
# Change either bound only with the other in view. The platform's stop-to-kill
# grace must exceed 120s for the graceful window to matter (deploy runbook).
# --access-logfile - emits request logs to stdout.
CMD exec gunicorn config.wsgi:application \
    --bind "[::]:${PORT:-8000}" \
    --access-logfile - \
    --timeout 120 \
    --graceful-timeout 120
