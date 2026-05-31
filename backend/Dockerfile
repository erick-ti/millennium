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

# collectstatic for prod (manifest storage). Inline placeholder env vars satisfy
# prod settings' fail-closed validators; none of these reach a network call.
RUN DJANGO_SETTINGS_MODULE=config.settings.prod \
    DJANGO_SECRET_KEY=collectstatic-build-only-not-real \
    DJANGO_ALLOWED_HOSTS=build \
    DJANGO_CSRF_TRUSTED_ORIGINS=https://build \
    DATABASE_URL=postgres://build:build@build:5432/build \
    REDIS_URL=redis://build:6379/0 \
    CELERY_BROKER_URL=redis://build:6379/1 \
    CELERY_RESULT_BACKEND=redis://build:6379/2 \
    python manage.py collectstatic --noinput

RUN useradd --create-home --shell /bin/bash --uid 1000 app \
    && chown -R app:app /app /opt/venv
USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
