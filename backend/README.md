# Millennium backend

Django 5.2 LTS + DRF + drf-spectacular + Celery + structlog.

## Local dev

```sh
make install      # uv sync — installs deps into backend/.venv
make dev          # docker compose up -d (postgres, redis, web, workers)
make migrate-up   # apply Django migrations
make superuser    # create admin user
```

URLs:
- http://localhost:8000/api/health/  — liveness
- http://localhost:8000/api/docs/    — Swagger UI
- http://localhost:8000/api/schema/  — OpenAPI schema (YAML)
- http://localhost:8000/admin/       — Django admin

## Tests

```sh
make test         # uses sqlite in-memory; no docker required
```

## Settings layout

`config/settings/{base,dev,prod,test}.py`. `manage.py` defaults to `dev`; pytest defaults to `test`. Override with `DJANGO_SETTINGS_MODULE`.
