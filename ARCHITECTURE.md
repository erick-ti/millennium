# Architecture

Millennium is a modular Django monolith that serves a JSON API, plus a Next.js
single-page frontend that consumes it. This document covers the runtime
topology, the data model, the scheduled jobs, the deploy flow, and the system
invariants that the code refers to by number.

## Overview

- **Backend:** Django 5.2 + Django REST Framework, organized as apps under
  `backend/apps/`: `core`, `cards`, `pricing`, `portfolio`, `collection`,
  `imports`, `valuation`, `alerts`, `decks`, `status`, `audit`. Each app owns
  its models, serializers, viewsets, and any sync or evaluation logic.
- **Frontend:** Next.js (App Router) + React + TypeScript under `frontend/`. It
  talks to the backend through a same-origin `/api/*` proxy so the browser sees
  one origin and cookies stay first-party.
- **API contract:** drf-spectacular emits an OpenAPI schema; `@hey-api/openapi-ts`
  generates a typed TypeScript client from it. Both the schema
  (`frontend/openapi.json`) and the generated client are committed, and CI fails
  if either drifts from the backend serializers.

## Runtime topology

**Development** (`make dev`, Docker Compose in `infra/docker-compose.yml`):
Postgres 16, Redis, the Django backend (`runserver`), a Celery worker, Celery
beat, and the Next.js dev server. Postgres, Redis, and the backend bind to
`127.0.0.1` only.

**Production** (config-as-code under `infra/hetzner/`): a standalone Caddy edge
is the only public listener. It terminates TLS (automatic certificates) and
reverse-proxies to the Next.js standalone frontend, which in turn proxies
`/api/*` to a gunicorn backend. Postgres runs alongside them. Nothing except the
Caddy edge is published to the public interface; the backend also listens on
`127.0.0.1:8001` for an SSH-tunnelled `/admin/`. Production does not run Redis or
a Celery worker: the cache is Django's database cache on Postgres, and the daily
jobs run as systemd timers instead of beat.

## Data model

A three-level card hierarchy with per-acquisition cost basis:

- **`cards`** are card identities (name, passcode, archetype).
- **`card_printings`** are a specific artwork at a specific rarity in a specific
  set. Their natural key is `(card, set_code, set_rarity, variant_label)`.
- **`collection_items`** are one holding: N copies of one printing in one
  condition/edition/language/portfolio.
- **`collection_lots`** record each acquisition (quantity, unit cost, date)
  under an item, so an item's quantity and cost roll up from its lots rather
  than being stored on the item.
- **`external_price_ids`** map a printing to each provider's product ID.
- **`price_snapshots`** are append-only daily prices per printing and edition,
  each with a confidence score. Unknown prices are stored as NULL, never zero,
  so a consumer can tell "unpriced" from "worth nothing".
- **`portfolio_value_snapshots`** are append-only daily valuations that carry
  coverage counts, so a rolled-up total never silently looks complete when some
  cards are unpriced.

TCGCSV is the MVP pricing source; YGOPRODeck supplies metadata only.

## Scheduled jobs

Four daily jobs run in sequence: YGOPRODeck metadata sync, TCGCSV price
reconcile-then-ingest, portfolio valuation, and price-alert evaluation. Each
runs through an orchestration entry point (not the bare functions) that applies
a cardinality guard, takes a per-kind Postgres advisory lock so a concurrent run
skips rather than doubling up, and records the outcome in append-only run
history (`SyncRun`, `ValuationRun`, `AlertRun`). Valuation refuses to run unless
a successful same-day pricing run exists, so it never values against stale
prices.

In development these are Celery beat tasks. In production they are systemd
timers. The upstream syncs use `Persistent=true` so they catch up after box
downtime; the dependent jobs (valuation, alerts) use `Persistent=false` so they
do not fire out of order against a stale upstream.

## Deployment and continuous delivery

The deploy config lives under `infra/hetzner/`: the app and edge Compose files,
the systemd units, `deploy.sh` (idempotent, with a `caddy validate` gate before
reloading the edge), and `backup_db.sh`.

Deployment is pull-based. A `millennium-deploy.timer` runs `deploy_poll.sh`
every couple of minutes on the box: it fetches `origin/main`, and if the tip has
advanced past the recorded deployed commit it checks out the new commit and runs
`deploy.sh`. The recorded "deployed commit" marker is the source of truth for
what is live, not git `HEAD`: the poller writes the marker only after `deploy.sh`
and a public-route health probe both pass, so a failed deploy retries on the
next tick instead of reporting a false success. Nothing pushes into the box, so
no inbound deploy credential lives on it. A rollback is a `git revert` on
`origin/main`, which the poller then deploys like any other change.

## Monitoring and backups

- **Host metrics:** a small `/proc` collector runs on the host every couple of
  minutes and ingests a metric sample into Postgres through a one-off container,
  feeding the read-only `/status` dashboard.
- **Uptime monitoring:** the backup and deploy jobs each ping a dead-man's-switch
  monitor on success, so a silent failure raises an alert.
- **Backups:** `backup_db.sh` runs a daily `pg_dump` to off-box object storage.
  It fails closed: without a reachable off-box remote it reports failure rather
  than a false success, and it does not prune local dumps until the upload is
  confirmed. Restores are tested.

## Invariants

These are constraints that are not obvious from reading a single file and that
cause real breakage if forgotten. The code refers to them by number; the
numbering is stable, so a comment that cites "invariant 7" means the entry
below.

1. **Server entrypoints fail closed if `DJANGO_SETTINGS_MODULE` is unset.** The
   check lives in `backend/config/__init__.py` and raises, and wsgi/asgi/celery
   inherit it through the import chain. This prevents Django's scaffold default
   (`os.environ.setdefault(..., "config.settings.dev")`) from silently booting
   dev settings in production, which would run with `DEBUG=True`, a wide-open
   `ALLOWED_HOSTS`, and a predictable secret. `manage.py` is the one deliberate
   exception, as a developer tool.

2. **`backend/config/settings/base.py` defines no environment-sensitive values.**
   Not `SECRET_KEY`, `DATABASES`, `CACHES`, `CELERY_BROKER_URL`,
   `CELERY_RESULT_BACKEND`, or `ALLOWED_HOSTS`. Each environment module
   (`dev`/`prod`/`test`) owns these. If `base.py` supplied a default, `prod.py`
   would inherit it and a missing production secret would no longer fail closed.

3. **`base.py` does not load any dotenv file.** `environ.Env.read_env(...)` lives
   in `dev.py` only. Otherwise a developer running the prod settings locally with
   a dev `.env` present would silently inherit the dev secret and localhost
   service URLs.

4. **Lint must pass on every commit.** `make lint` runs ruff and mypy in strict
   mode. A documented gate that is allowed to fail is worse than no gate, because
   contributors learn to bypass it.

5. **Compose port mappings for `postgres`, `redis`, and the dev `backend`
   service bind to `127.0.0.1` only,** never `0.0.0.0` or the bare `port:port`
   form. Postgres ships with default credentials, Redis has no password, and the
   dev backend runs with `DEBUG=True` (so 500-page tracebacks leak environment
   values). Binding to all interfaces would expose them on any shared network.

6. **HSTS in `prod.py` defaults off** (`SECURE_HSTS_SECONDS=0`, and the
   include-subdomains and preload flags off), each overridable via
   `DJANGO_SECURE_HSTS_*`. Opting into a long max-age with preload before HTTPS
   is proven on every subdomain pins browsers (and the global preload list) into
   HTTPS for up to a year, and recovery means waiting out the timer. In the live
   deployment, HSTS is applied at the Caddy edge instead, because the frontend
   proxy hop drops `X-Forwarded-Proto` and Django would otherwise see the request
   as insecure.

7. **The OpenAPI schema and docs require authentication and exclude the
   read-only demo.** drf-spectacular does not inherit
   `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]`, so its permission is set
   explicitly in `SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"]` to a permission that
   both requires auth and denies the demo account. The schema describes every
   endpoint, field, and filter, which is reconnaissance material for a private
   app; the demo is a publicly obtainable session, so plain "authenticated" would
   expose the whole surface through it.

8. **The image's Python virtualenv lives at `/opt/venv`, not `/app/.venv`,** and
   no Compose volume mounts anything at `/app/.venv`. Docker copies image data
   into a named volume only when the volume is first created; a later rebuild
   produces a new image but the container still mounts the stale volume on top,
   so backend, worker, and beat would silently run old packages.

9. **Changes reach `main` only through the reviewed pull-request flow.** The
   `protect-main` ruleset blocks force-pushes and deletions and requires the
   status checks to pass; merges are squash-merges of reviewed PRs. Because
   continuous delivery auto-deploys `main`, anything that lands there ships.

10. **`CSRF_TRUSTED_ORIGINS` must include every frontend origin that POSTs
    through the Next.js `/api/*` proxy** (env-driven in `dev.py`, required with no
    default in `prod.py`). The proxy rewrites the Host for Django but the browser
    still sends its own `Origin`, and Django's CSRF origin check rejects the
    mismatch on every unsafe method, silently returning 403 on writes.

11. **The session cookie stays HttpOnly and the CSRF cookie stays non-HttpOnly;
    never override either.** The Django defaults are correct and deliberately
    left unset. Making the session cookie readable would let an XSS exfiltrate it
    (account takeover); making the CSRF cookie HttpOnly would stop the proxy from
    reading the token to echo into `X-CSRFToken`, so every unsafe write would
    403.

12. **The edge Caddy Compose mounts the `caddy/` directory, not the single
    `Caddyfile`.** A single-file bind mount pins Docker to the file's inode at
    container-create time, but the deploy replaces the file (new inode) on every
    pull, so a single-file mount would serve the stale config forever and
    `caddy reload` would report no change. Mounting the directory avoids this.

13. **The `/ops` observability read endpoints set
    `permission_classes = [IsAuthenticated, IsSuperUser]` explicitly,** never the
    global default. The global default is passed by the read-only demo for safe
    methods, so an `/ops`-adjacent GET that inherited it would let the demo (a
    publicly obtainable session) read the audit trail and error logs.
