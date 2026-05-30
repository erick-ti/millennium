/**
 * Public API surface for the typed Millennium client.
 *
 * The generated tree under `./generated/` is a regeneration target (DECISIONS
 * 2026-05-27 Phase 4 slice 2):
 *
 *   1. `make frontend-snapshot-schema`  — refresh `frontend/openapi.json` from
 *      Django via `manage.py spectacular`.
 *   2. `make frontend-gen-api`          — regenerate `./generated/` via
 *      `@hey-api/openapi-ts`.
 *
 * Both the schema snapshot AND the generated output are committed so PR diffs
 * show every API surface change (the user-settled schema-acquisition strategy).
 *
 * ─────────────────────────────────────────────────────────────────────────
 * WRITES ARE LIVE (slice 6) — but the barrel is still an explicit allowlist.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Slice 6 wired `X-CSRFToken` injection in `frontend/src/proxy.ts`, so the
 * import write helpers (`importsRowsApproveCreate` / `OverrideCreate` /
 * `RejectCreate`, `importsBatchesCreate`) + their `*Mutation` variants are now
 * re-exported and safe to call through the same-origin proxy.
 *
 * We deliberately do NOT collapse this to `export * from "./generated"` (the
 * slice-2 comment's suggested one-line revert): hey-api still generates the
 * `*InfiniteOptions` / `*InfiniteQueryKey` helpers with TanStack v5's required
 * `initialPageParam` / `getNextPageParam` suppressed by `// @ts-ignore`, so they
 * compile but can't paginate past page 1 (Codex slice 2 round 7). They remain
 * filtered out below until a project-owned wrapper derives `getNextPageParam`
 * from DRF's `next` field — and the views ship page-number navigation, so that
 * wrapper isn't needed yet. The allowlist is the durable shape; adding a new
 * read endpoint means adding its name here (a missing name is a loud TS error
 * at the call site, not a silent gap).
 *
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Consumers `import { foo } from "@/lib/api"`; the generated path stays an
 * implementation detail of the codegen target.
 */

// ─── Types (safe; typing doesn't fire requests) ───────────────────────────
export type * from "./generated/types.gen";

// ─── SDK functions: reads + the slice-6 import writes ──────────────────────
// `csrfRetrieve` seeds the CSRF cookie (called once on app load). The four
// import write fns (`importsBatchesCreate` upload + approve/override/reject)
// are now CSRF-safe via `proxy.ts`. `type Options` is shared and type-only.
export {
  cardsCardsList,
  cardsCardsRetrieve,
  cardsPrintingsList,
  cardsPrintingsRetrieve,
  collectionItemsList,
  collectionItemsRetrieve,
  collectionLotsList,
  collectionLotsRetrieve,
  csrfRetrieve,
  healthRetrieve,
  importsBatchesCreate,
  importsBatchesList,
  importsBatchesRetrieve,
  importsRowsApproveCreate,
  importsRowsList,
  importsRowsOverrideCreate,
  importsRowsRejectCreate,
  importsRowsRetrieve,
  type Options,
  portfolioPortfoliosList,
  portfolioPortfoliosRetrieve,
  portfolioSnapshotsList,
  portfolioSnapshotsRetrieve,
  pricingSnapshotsLatestRetrieve,
  pricingSnapshotsList,
  pricingSnapshotsRetrieve,
} from "./generated/sdk.gen";

// ─── TanStack Query helpers ────────────────────────────────────────────────
// Read `*Options` + `*QueryKey` (page-number form — `options.query.page`, what
// DRF's PageNumberPagination serves) and the slice-6 import write `*Mutation`
// helpers. The `*InfiniteOptions` / `*InfiniteQueryKey` variants stay omitted
// (broken past page 1 — see the header note); add a project-owned wrapper if
// infinite scroll is ever wanted.
export {
  cardsCardsListOptions,
  cardsCardsListQueryKey,
  cardsCardsRetrieveOptions,
  cardsCardsRetrieveQueryKey,
  cardsPrintingsListOptions,
  cardsPrintingsListQueryKey,
  cardsPrintingsRetrieveOptions,
  cardsPrintingsRetrieveQueryKey,
  collectionItemsListOptions,
  collectionItemsListQueryKey,
  collectionItemsRetrieveOptions,
  collectionItemsRetrieveQueryKey,
  collectionLotsListOptions,
  collectionLotsListQueryKey,
  collectionLotsRetrieveOptions,
  collectionLotsRetrieveQueryKey,
  csrfRetrieveOptions,
  csrfRetrieveQueryKey,
  healthRetrieveOptions,
  healthRetrieveQueryKey,
  importsBatchesCreateMutation,
  importsBatchesListOptions,
  importsBatchesListQueryKey,
  importsBatchesRetrieveOptions,
  importsBatchesRetrieveQueryKey,
  importsRowsApproveCreateMutation,
  importsRowsListOptions,
  importsRowsListQueryKey,
  importsRowsOverrideCreateMutation,
  importsRowsRejectCreateMutation,
  importsRowsRetrieveOptions,
  importsRowsRetrieveQueryKey,
  portfolioPortfoliosListOptions,
  portfolioPortfoliosListQueryKey,
  portfolioPortfoliosRetrieveOptions,
  portfolioPortfoliosRetrieveQueryKey,
  portfolioSnapshotsListOptions,
  portfolioSnapshotsListQueryKey,
  portfolioSnapshotsRetrieveOptions,
  portfolioSnapshotsRetrieveQueryKey,
  pricingSnapshotsLatestRetrieveOptions,
  pricingSnapshotsLatestRetrieveQueryKey,
  pricingSnapshotsListOptions,
  pricingSnapshotsListQueryKey,
  pricingSnapshotsRetrieveOptions,
  pricingSnapshotsRetrieveQueryKey,
  type QueryKey,
} from "./generated/@tanstack/react-query.gen";

// ─── Runtime client singleton ─────────────────────────────────────────────
// Re-exported so consumers can call `client.setConfig({...})` for base URL,
// auth interceptors, etc. Same client all SDK functions use; consumers
// should NOT create a separate client (would lose the shared config).
export { client } from "./generated/client.gen";
