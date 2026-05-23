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

**Phase 1B: Core data model.** cards, card_printings, external_price_ids, portfolios, collection_items, collection_lots, storage_locations, price_snapshots, portfolio_value_snapshots. First migrations, base model mixin, constraints, enum definitions, seed data. Implements the eight 2026-05-18 schema decisions in `DECISIONS.md`.

## Completed milestones

- **Phase 1A: Project scaffold** (completed 2026-05-03, commits `6a33c4a` → `e5c2dc9`). Django 5.2 with config/ split, DRF + drf-spectacular, `/api/health/`, Docker Compose (Postgres 16 + Redis + backend + celery worker + beat), Makefile, pyproject.toml with ruff/mypy/pytest, 3 passing tests, admin/auth, structlog. Six rounds of adversarial review hardening.
- **Phase 1A.5: Data reconnaissance spike** (completed 2026-05-18). Real Dragon Shield CSV, TCGCSV product/price data (8 sets across four eras), and YGOPRODeck full card dump inspected. End-to-end pipeline validated 7/7 with the `"Prismatic "` rarity fallback rule. Eight schema decisions recorded in `DECISIONS.md` covering cards PK, card_printings natural key, external_price_ids, collection_items/lots layout, edition placement, price_snapshots structure, DS-folder→portfolio mapping, and normalized_name indexing. Findings doc at `docs/recon/PHASE_1A5_FINDINGS.md` (gitignored per project doc-layer convention).

## Upcoming milestones

1. **Phase 2: Data pipeline.** YGOPRODeck metadata sync, TCGCSV daily price ingestion, Celery task wiring, price snapshot storage with confidence scoring, provider adapter pattern.
2. **Phase 3: CSV import.** Dragon Shield CSV parser, column mapping, card-to-printing matching engine, review queue API, import batch/row storage.
3. **Phase 4: Frontend MVP.** Next.js scaffold, OpenAPI client generation, collection view, card detail with price history, portfolio summary, import upload + match review UI.
4. **Phase 5: Portfolio analytics.** Archetype tagging, deck association, biggest movers, price alerts, advanced filtering. Minimal Playwright smoke tests after UI stabilizes.

## Non-goals

- **Multi-tenant / custom accounts.** Single user. Django's built-in auth + admin only.
- **Real-time pricing.** Scheduled refresh. No websockets, no polling.
- **Card scanner / OCR.** CSV import only from external apps.
- **Marketplace.** No buy/sell/trade workflows. No transactions table.
- **Deck builder.** Deck-awareness is a portfolio feature, not a construction tool.
- **Scraping.** Official APIs, licensed APIs, daily CSV feeds, and user-provided exports only.

## Open questions

1. **Condition adjustment factors.** Multipliers for LP/MP/HP/DMG when only product-level pricing is available. Phase 2-blocking (valuation needs them); not Phase 1B-blocking.
2. **OpenAPI client generation tooling.** openapi-typescript-codegen vs orval vs alternatives. Phase 4-blocking.

## Architecture direction

Modular Django monolith. Backend organized as apps: core, cards, portfolio, collection, imports, pricing, valuation. Three-level data hierarchy: cards → card_printings → collection_items, with collection_lots for per-acquisition cost basis and external_price_ids for multi-provider pricing. Append-only price_snapshots with confidence scoring. TCGCSV as MVP pricing source (YGOPRODeck for metadata only). Portfolio-level daily value snapshots with valuation versioning. Railway deployment first, AWS migration as a dedicated infrastructure phase.
