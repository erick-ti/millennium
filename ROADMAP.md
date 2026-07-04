# Millennium

## Vision

A personal Yu-Gi-Oh collection tracker that treats a card collection like an investment portfolio: per-lot cost-basis tracking, confidence-scored pricing from multiple sources, and historical valuation analytics. Cards are imported via CSV from external scanner apps, and pricing refreshes on a daily schedule rather than in real time.

## Stack

- **Frontend:** Next.js (App Router) + React + TypeScript
- **UI:** Tailwind CSS + shadcn/ui + TanStack Table + TanStack Query + Recharts
- **Backend:** Django 5.2 LTS + Django REST Framework + drf-spectacular
- **Database:** PostgreSQL 16+
- **Cache / queue:** Redis (dev cache and broker); production drops Redis and uses Django's database cache on Postgres (no Celery worker under the timer topology)
- **Scheduled jobs:** Celery + Celery Beat (dev); systemd timers (production)
- **API contract:** OpenAPI schema generates a typed TypeScript client, committed and drift-gated
- **Testing:** pytest (backend), Vitest + React Testing Library (frontend), Playwright (end-to-end smoke)
- **Containerization:** Docker + Docker Compose
- **CI:** GitHub Actions
- **Deployment:** self-hosted VPS behind a Caddy edge (phase 1); AWS ECS/Fargate + RDS + ElastiCache is the planned phase-2 target
- **IaC:** Terraform (AWS phase only)

## Current state

The application is live at https://millennium.erickti.com and there is no active build milestone. Deployment, the bespoke "Vault" visual identity, the public read-only demo, and the superuser operations console are all complete (see Completed milestones). The AWS migration under Upcoming milestones is the next dedicated phase when chosen.

The live deployment runs frontend, backend, and Postgres on a single VPS behind a standalone Caddy edge (automatic TLS, the only public listener). The daily sync, valuation, and alert management commands run as systemd timers rather than an always-on worker; Redis is not used in production (the cache is Django's database cache on the existing Postgres). Pull-based continuous deployment ships merges to `main` to the box within a couple of minutes: a poller checks out the new commit, deploys, gates on a public-route health probe, and records the deployed commit only after both succeed. HTTPS is enforced with HSTS at the Caddy edge (one-year max-age, no preload). Off-box database backups run daily and are restore-tested. Railway was evaluated and fully repo-prepped as a managed-PaaS alternative (see below); the VPS is the live target.

## Completed milestones

- **Phase 1A: Project scaffold** (completed 2026-05-03, commits `6a33c4a` to `e5c2dc9`). Django 5.2 with a split settings config, DRF + drf-spectacular, `/api/health/`, Docker Compose (Postgres 16 + Redis + backend + Celery worker + beat), Makefile, and pyproject.toml with ruff/mypy/pytest. Admin, auth, and structlog wired; first tests passing.
- **Phase 1A.5: Data reconnaissance spike** (completed 2026-05-18). Real Dragon Shield CSV, TCGCSV product and price data (eight sets across four eras), and a full YGOPRODeck card dump were inspected end to end. The pipeline validated against real data with the Prismatic-rarity fallback rule, producing eight schema decisions covering the cards primary key, the card-printings natural key, external price IDs, the collection-items and lots layout, edition placement, price-snapshot structure, the Dragon-Shield-folder to portfolio mapping, and normalized-name indexing.
- **Phase 1B: Core data model** (completed 2026-05-23, PRs #4 to #14). All nine models on a shared `TimeStampedModel` base (cards, card printings, external price IDs, portfolios, storage locations, collection items, collection lots, price snapshots, portfolio value snapshots), with natural-key UNIQUE plus enum and value CHECK constraints, deliberate FK delete semantics (PROTECT for valuable downstream data, CASCADE for composition), shared enums (Edition/Provider/Condition/Language), and append-only snapshot tables whose admins block delete and edit. pytest-on-Postgres and a gitleaks secret scan gate every merge.
- **Phase 2: Data pipeline** (completed 2026-05-25, PRs #15 to #21). A provider adapter pattern, YGOPRODeck metadata sync, TCGCSV reconcile-then-ingest pricing, and daily Celery-beat scheduling (02:00/03:00/04:00) under cardinality guards and per-kind advisory locks with append-only `SyncRun` history. The valuation engine produces `PortfolioValueSnapshot` rows with partial-coverage accounting plus `ValuationRun` history.
- **Phase 3: CSV import** (completed 2026-05-27, PRs #22 to #26). The `imports` app: `ImportBatch`/`ImportRow` JSON-staging models, a Dragon Shield parser and normalizer, an alias-aware card-to-printing matcher (with a multi-variant guard), `run_import` orchestration and materialization (a per-printing reconciliation-coverage gate and per-holding re-import dedup), and the DRF review-queue API (list/filter, approve/override/reject routed through a single materialization chokepoint).
- **Phase 4: Frontend MVP** (completed 2026-05-30, PRs #27 to #32). A Next.js 16 (App Router) + React 19 scaffold with a same-origin `/api/*` proxy; a read-only DRF API (cards/collection/portfolio/pricing) plus `@hey-api/openapi-ts` client generation behind a committed-snapshot drift gate; the collection view (a reusable `<DataTable>` plus a Vitest/RTL harness); card detail and price history; a portfolio summary grid with a coverage-aware value chart; and the import upload and match-review UI carrying the first browser writes and CSRF flow (an `X-CSRFToken`-injecting proxy, a CSRF cookie seed endpoint, and a synchronous upload endpoint).
- **Auth/login slice** (completed 2026-05-31, PR #33). Custom Django session-cookie endpoints (login/logout/me; AllowAny plus `csrf_protect` login, throttled) with a `/login` page, an `AuthProvider`, and a global client-side 403-to-login gate. The app became usable end to end in a browser, exercising the CSRF and write plumbing against a real session.
- **Phase 5: Portfolio analytics** (completed 2026-06-12, PRs #34 to #39). Archetype tagging, advanced collection filtering, a biggest-movers endpoint, percent-move price alerts, deck association, and a Playwright smoke suite (login to import to approve to collection, plus deck flows) backed by a guarded `seed_smoke` command and an advisory e2e CI job.
- **Bespoke "Vault" visual identity** (completed 2026-06-19, PRs #46 and #57). An Egyptian-vault-meets-Bloomberg-terminal identity replacing the default shadcn/Geist look: a public landing page and a dark aged-gold-on-tomb-black design system with the Eye of Wadjet as a functional sync mark and one foil card (#46), then the per-component elevation of every authenticated view to that bar (a shared page header, lit `.vitrine` panels, a trading-desk table treatment, an area-gradient chart, colorblind-safe deltas, and vault-themed 404 and error pages) (#57). Restraint is the thesis: one gold accent, hairline ornament, no drop shadows. Reviewed with a Playwright and axe accessibility pass (0 violations).
- **Read-only demo** (completed 2026-06-21, PR #60). A one-click "Enter the vault" demo so the full authenticated app is explorable without credentials, while the security architecture stays intact: a public password-less `POST /api/auth/demo-login/`, a single `DemoReadOnly` permission ANDed into the global defaults that read-only-locks the demo across every endpoint, a schema guard keeping the demo out of the OpenAPI docs, and a server-derived `is_demo` flag the SPA uses to hide write affordances. The demo is single-tenant: it shows the owner's real data read-only, not per-user isolated data. It is seeded create-only by the deploy migrate step.
- **Superuser operations console** (completed 2026-07-04, PR #61). A new `audit` app plus a superuser-gated `/ops` console: an append-only `AuditEvent` trail (one row per unsafe request, with actor attribution that survives logout) and a fingerprint-grouped `ErrorLog` (backend exceptions and 5xx, plus a public frontend error beacon), read-only over `/api/audit/` behind a superuser permission, with a daily prune timer. The public error beacon is bounded against abuse with a body-size cap, throttling, and a per-day public quota; retention windows fail closed on a misconfigured value.

## Upcoming milestones

1. **AWS migration (infrastructure phase).** ECS/Fargate + RDS + ElastiCache via Terraform, the phase-2 deployment target (see Stack). A dedicated infrastructure phase, not feature work.

## Deferred (parking lot)

- **Visitor analytics (superuser).** Unique-visitor and geolocation metrics plus page and action counts, sourced from the Caddy edge access logs (Django cannot see real client IPs behind the proxy) and ingested through the same host-metrics timer pattern. Requires privacy hardening (geo-enrich at ingestion, store derived city and coarse location plus a salted IP hash, never the raw IP, with short retention). Build-vs-buy is open: a bespoke log pipeline versus a self-hosted Plausible or Umami.
- **Operations console polish.** A severity-summary header widget and correlation of errors to the deployed commit (the deploy commit is already collected).
- **Prune-audit monitoring.** The audit-prune timer has no dead-man's-switch yet (unlike the backup and deploy timers), so a silent prune failure would not alert. Low stakes since the prune is idempotent.

## Non-goals

- **Multi-tenant / custom accounts.** Single user. Django's built-in auth and admin only. (The read-only demo is a deliberate, bounded exception: a single seeded `demo` persona that shares the owner's data read-only, not per-user data isolation or multi-tenancy.)
- **Real-time pricing.** Scheduled refresh only. No websockets, no polling.
- **Card scanner / OCR.** CSV import only, from external apps.
- **Marketplace.** No buy/sell/trade workflows and no transactions table.
- **Deck builder.** Deck-awareness is a portfolio feature, not a construction tool.
- **Scraping.** Official APIs, licensed APIs, daily CSV feeds, and user-provided exports only.

## Architecture direction

A modular Django monolith, organized as apps: core, cards, portfolio, collection, imports, pricing, valuation, and the analytics apps. A three-level data hierarchy (cards to card printings to collection items) sits alongside collection lots for per-acquisition cost basis and external price IDs for multi-provider pricing. Price snapshots are append-only with confidence scoring (TCGCSV is the MVP pricing source; YGOPRODeck supplies metadata only). Portfolios get daily value snapshots with valuation versioning. Self-hosted VPS deployment comes first, with the AWS migration as a dedicated later phase. See [ARCHITECTURE.md](ARCHITECTURE.md) for the runtime topology, the system invariants, and the deploy flow.
