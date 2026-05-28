# Millennium

## Vision

A personal Yu-Gi-Oh collection portfolio tracker that treats a card collection like an investment portfolio — per-lot cost-basis tracking, confidence-scored pricing from multiple sources, and historical valuation analytics. Cards are imported via CSV from external scanner apps; pricing is refreshed on a daily schedule, not real-time.

## Stack

- **Frontend:** Next.js (App Router) + React + TypeScript
- **UI:** Tailwind CSS + shadcn/ui + TanStack Table + TanStack Query + Recharts
- **Backend:** Django 5.2 LTS + Django REST Framework + drf-spectacular
- **Database:** PostgreSQL 16+
- **Queue/Cache:** Redis
- **Workers:** Celery + Celery Beat
- **API contract:** OpenAPI spec → generated TypeScript client
- **Testing:** pytest (backend), Vitest + React Testing Library (frontend)
- **Containerization:** Docker + docker-compose
- **CI:** GitHub Actions
- **Deployment:** Railway (phase 1), AWS ECS/Fargate + RDS + ElastiCache (phase 2)
- **IaC:** Terraform (AWS phase only)

## Current milestone

**Phase 4: Frontend MVP.** Next.js (App Router) scaffold, OpenAPI client generation, collection view, card detail with price history, portfolio summary, import upload + match-review UI.

## Completed milestones

- **Phase 1A: Project scaffold** (completed 2026-05-03, commits `6a33c4a` → `e5c2dc9`). Django 5.2 with config/ split, DRF + drf-spectacular, `/api/health/`, Docker Compose (Postgres 16 + Redis + backend + celery worker + beat), Makefile, pyproject.toml with ruff/mypy/pytest, 3 passing tests, admin/auth, structlog. Six rounds of adversarial review hardening.
- **Phase 1A.5: Data reconnaissance spike** (completed 2026-05-18). Real Dragon Shield CSV, TCGCSV product/price data (8 sets across four eras), and YGOPRODeck full card dump inspected. End-to-end pipeline validated 7/7 with the `"Prismatic "` rarity fallback rule. Eight schema decisions recorded in `DECISIONS.md` covering cards PK, card_printings natural key, external_price_ids, collection_items/lots layout, edition placement, price_snapshots structure, DS-folder→portfolio mapping, and normalized_name indexing. Findings doc at `docs/recon/PHASE_1A5_FINDINGS.md` (gitignored per project doc-layer convention).
- **Phase 1B: Core data model** (completed 2026-05-23, PRs #4–#14). All nine models on the `TimeStampedModel` base — cards, card_printings, external_price_ids, portfolios, storage_locations, collection_items, collection_lots, price_snapshots, portfolio_value_snapshots — with natural-key UNIQUE + enum/value CHECK constraints, deliberate FK delete semantics (PROTECT for valuable downstream data, CASCADE for composition), shared enums (Edition/Provider/Condition/Language), and append-only snapshot tables (admins block delete + edit). pytest-on-Postgres-16 + gitleaks gate every merge.
- **Phase 2: Data pipeline** (completed 2026-05-25, PRs #15–#21). Provider adapter pattern, YGOPRODeck metadata sync + TCGCSV reconcile→ingest, daily Celery-beat wiring (02:00/03:00/04:00) under cardinality guards + per-kind advisory locks with append-only `SyncRun` history, and the valuation engine — `PortfolioValueSnapshot` with partial-coverage accounting + `ValuationRun` run history.
- **Phase 3: CSV import** (completed 2026-05-27, PRs #22–#26). The `imports` app: `ImportBatch`/`ImportRow` JSON-staging models, Dragon Shield parser + normalization, the alias-aware card→printing matcher (`is_multi_variant` guard), `run_import` orchestration + materialization (per-printing reconciliation-coverage gate, per-holding re-import dedup), and the DRF review-queue API (list/filter, approve/override/reject through `_materialize`, schema gated per Invariant 7).

## Upcoming milestones

1. **Phase 5: Portfolio analytics.** Archetype tagging, deck association, biggest movers, price alerts, advanced filtering. Minimal Playwright smoke tests after UI stabilizes.

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
3. **OpenAPI client generation tooling.** openapi-typescript-codegen vs orval vs alternatives. Phase 4-blocking.

## Architecture direction

Modular Django monolith. Backend organized as apps: core, cards, portfolio, collection, imports, pricing, valuation. Three-level data hierarchy: cards → card_printings → collection_items, with collection_lots for per-acquisition cost basis and external_price_ids for multi-provider pricing. Append-only price_snapshots with confidence scoring. TCGCSV as MVP pricing source (YGOPRODeck for metadata only). Portfolio-level daily value snapshots with valuation versioning. Railway deployment first, AWS migration as a dedicated infrastructure phase.
