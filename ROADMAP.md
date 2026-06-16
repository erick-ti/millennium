# Millennium

## Vision

A personal Yu-Gi-Oh collection portfolio tracker that treats a card collection like an investment portfolio — per-lot cost-basis tracking, confidence-scored pricing from multiple sources, and historical valuation analytics. Cards are imported via CSV from external scanner apps; pricing is refreshed on a daily schedule, not real-time.

## Stack

- **Frontend:** Next.js (App Router) + React + TypeScript
- **UI:** Tailwind CSS + shadcn/ui + TanStack Table + TanStack Query + Recharts
- **Backend:** Django 5.2 LTS + Django REST Framework + drf-spectacular
- **Database:** PostgreSQL 16+
- **Queue/Cache:** Redis (dev cache/broker); prod drops Redis — Django DatabaseCache on Postgres (no Celery worker under the timer topology)
- **Workers:** Celery + Celery Beat
- **API contract:** OpenAPI spec → generated TypeScript client
- **Testing:** pytest (backend), Vitest + React Testing Library (frontend)
- **Containerization:** Docker + docker-compose
- **CI:** GitHub Actions
- **Deployment:** Self-hosted Hetzner VPS (phase 1, coexisting with another self-hosted project; Railway evaluated + repo-prepped as an alternative), AWS ECS/Fargate + RDS + ElastiCache (phase 2)
- **IaC:** Terraform (AWS phase only)

## Current milestone

**Self-hosted Hetzner VPS deployment (phase-1 deploy target) — pivoted from Railway (2026-06-14).** Deploy frontend + backend + Postgres on a **single Hetzner VPS coexisting with another self-hosted project**, behind a standalone Caddy edge (auto-TLS, the only public listener); the daily sync/valuation/alert management commands run as **systemd timers** (no always-on worker/beat, no Railway cron). **Redis is dropped** — prod cache is Django `DatabaseCache` on the existing Postgres (nothing dials a Celery broker under the timer topology). Chosen over Railway for a stronger recruiter-facing self-hosted infrastructure story (a coherent one-standard-across-projects server, not a PaaS click-deploy). Config-as-code under `infra/hetzner/`; the step-by-step deploy runbook is kept local (it carries box-specific operational detail). Merged in #45; **provisioned LIVE 2026-06-15** at https://millennium.erickti.com — verified end-to-end (HTTPS + Let's Encrypt cert, catalog seeded 14,388 cards / 51,481 prices, 5 systemd timers armed, R2 backups restore-tested) and doppel-safe (public surface 80/443/22 only, `/api/schema/` 403 anon, doppel containers never touched). **Remaining:** post-deploy hardening (empirical XFF spoof test → `DJANGO_NUM_PROXIES`, HSTS ramp once HTTPS is proven) + confirm the first systemd timer cycle (2026-06-16).

**Railway was evaluated + fully repo-prepped (#40–#44), now retained as the evaluated managed-PaaS alternative:** prod settings (env-tunable cookie SameSite + `DJANGO_NUM_PROXIES` knob), the backend + frontend production images, the CI image-build gate (`images.yml`, a required check), Railway config-as-code (`infra/railway/*.railway.json`), and `docs/railway-deploy-runbook.md` (banner-marked superseded; its env matrix predates the DatabaseCache change). Kept as a documented alternative + portfolio artifact, not the live target.

## Completed milestones

- **Phase 1A: Project scaffold** (completed 2026-05-03, commits `6a33c4a` → `e5c2dc9`). Django 5.2 with config/ split, DRF + drf-spectacular, `/api/health/`, Docker Compose (Postgres 16 + Redis + backend + celery worker + beat), Makefile, pyproject.toml with ruff/mypy/pytest, 3 passing tests, admin/auth, structlog. Six rounds of adversarial review hardening.
- **Phase 1A.5: Data reconnaissance spike** (completed 2026-05-18). Real Dragon Shield CSV, TCGCSV product/price data (8 sets across four eras), and YGOPRODeck full card dump inspected. End-to-end pipeline validated 7/7 with the `"Prismatic "` rarity fallback rule. Eight schema decisions recorded in `DECISIONS.md` covering cards PK, card_printings natural key, external_price_ids, collection_items/lots layout, edition placement, price_snapshots structure, DS-folder→portfolio mapping, and normalized_name indexing. Findings doc at `docs/recon/PHASE_1A5_FINDINGS.md` (gitignored per project doc-layer convention).
- **Phase 1B: Core data model** (completed 2026-05-23, PRs #4–#14). All nine models on the `TimeStampedModel` base — cards, card_printings, external_price_ids, portfolios, storage_locations, collection_items, collection_lots, price_snapshots, portfolio_value_snapshots — with natural-key UNIQUE + enum/value CHECK constraints, deliberate FK delete semantics (PROTECT for valuable downstream data, CASCADE for composition), shared enums (Edition/Provider/Condition/Language), and append-only snapshot tables (admins block delete + edit). pytest-on-Postgres-16 + gitleaks gate every merge.
- **Phase 2: Data pipeline** (completed 2026-05-25, PRs #15–#21). Provider adapter pattern, YGOPRODeck metadata sync + TCGCSV reconcile→ingest, daily Celery-beat wiring (02:00/03:00/04:00) under cardinality guards + per-kind advisory locks with append-only `SyncRun` history, and the valuation engine — `PortfolioValueSnapshot` with partial-coverage accounting + `ValuationRun` run history.
- **Phase 3: CSV import** (completed 2026-05-27, PRs #22–#26). The `imports` app: `ImportBatch`/`ImportRow` JSON-staging models, Dragon Shield parser + normalization, the alias-aware card→printing matcher (`is_multi_variant` guard), `run_import` orchestration + materialization (per-printing reconciliation-coverage gate, per-holding re-import dedup), and the DRF review-queue API (list/filter, approve/override/reject through `_materialize`, schema gated per Invariant 7).
- **Phase 4: Frontend MVP** (completed 2026-05-30, PRs #27–#32). Next.js 16 (App Router) + React 19 scaffold with a same-origin `/api/*` proxy; read-only DRF API (cards/collection/portfolio/pricing) + `@hey-api/openapi-ts` client generation behind a committed-snapshot drift gate; collection view (reusable `<DataTable>` + Vitest/RTL harness); card detail + price history; portfolio summary grid + coverage-aware value chart; and the import upload + match-review UI carrying the first browser writes + CSRF (`proxy.ts` X-CSRFToken injection, `GET /api/csrf/` cookie seed, synchronous upload endpoint). Per-slice Codex adversarial review throughout.
- **Auth/login slice** (completed 2026-05-31, PR #33). Custom Django session-cookie endpoints (login/logout/me; AllowAny + `csrf_protect` login, throttled) + a `/login` page, `AuthProvider`, and a global client-side 403→/login gate. The app is now usable end-to-end in a browser; slice-6's CSRF/write plumbing is exercisable against a real session. Six Codex adversarial-challenge rounds.
- **Phase 5: Portfolio analytics** (completed 2026-06-12, PRs #34–#39). Archetype tagging, advanced collection filtering, biggest movers, price alerts, deck association, and a Playwright smoke suite (login→import→approve→collection + deck flows) with a guarded `seed_smoke` command and an advisory e2e CI job. Per-slice Codex adversarial review throughout.

## Upcoming milestones

1. **AWS migration (infrastructure phase).** ECS/Fargate + RDS + ElastiCache via Terraform — the phase-2 deployment target (see Stack). A dedicated infrastructure phase, not feature work.

## Non-goals

- **Multi-tenant / custom accounts.** Single user. Django's built-in auth + admin only.
- **Real-time pricing.** Scheduled refresh. No websockets, no polling.
- **Card scanner / OCR.** CSV import only from external apps.
- **Marketplace.** No buy/sell/trade workflows. No transactions table.
- **Deck builder.** Deck-awareness is a portfolio feature, not a construction tool.
- **Scraping.** Official APIs, licensed APIs, daily CSV feeds, and user-provided exports only.

## Open questions

1. **Condition adjustment factors** — **resolved 2026-05-25** (slice 4b): the valuation engine applies a hardcoded, version-tagged DS-condition→factor table (NM 1.00 → Poor 0.40); see DECISIONS 2026-05-25. (Was: multipliers for LP/MP/HP/DMG when only product-level pricing is available.)
2. **Valuation coverage representation** — **resolved 2026-05-25** (slice 4a): `portfolio_value_snapshots` carries card-quantity coverage counts (`total/priced/costed_card_count`) + a nullable `unrealized_gain` set iff fully covered (CHECK `gain_iff_complete`); unknowns are excluded from totals, never zeroed. See DECISIONS 2026-05-25. (Was: how partial/unknown inputs are recorded so a rolled-up total doesn't silently look complete.)
3. **OpenAPI client generation tooling** — **resolved 2026-05-27** (Phase 4 slice 1): `@hey-api/openapi-ts` (the actively-maintained successor to the deprecated `openapi-typescript-codegen` originally listed; TanStack Query plugin available). See DECISIONS 2026-05-27. (Was: openapi-typescript-codegen vs orval vs alternatives. Phase 4-blocking.)
4. **Frontend auth/login** — **resolved 2026-05-31** (PR #33): shipped as a dedicated slice *before* Phase 5 — custom Django session-cookie auth (login/logout/me) + a `/login` page + client-side 403→/login gating; see DECISIONS 2026-05-30. (Was: the API is `IsAuthenticated` with no frontend login, so every live page 403s until a session exists on the frontend origin — does a minimal login slice come before Phase 5, or fold into it?)

## Architecture direction

Modular Django monolith. Backend organized as apps: core, cards, portfolio, collection, imports, pricing, valuation. Three-level data hierarchy: cards → card_printings → collection_items, with collection_lots for per-acquisition cost basis and external_price_ids for multi-provider pricing. Append-only price_snapshots with confidence scoring. TCGCSV as MVP pricing source (YGOPRODeck for metadata only). Portfolio-level daily value snapshots with valuation versioning. Self-hosted Hetzner VPS deployment first (Railway evaluated + repo-prepped as the alternative), AWS migration as a dedicated infrastructure phase.
