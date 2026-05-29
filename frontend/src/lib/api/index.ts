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
 * SLICE 2 SCOPE: READ-ONLY operations only.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * The three import-review write helpers — `importsRowsApproveCreate`,
 * `importsRowsOverrideCreate`, `importsRowsRejectCreate` — and their
 * TanStack Query mutation variants (`*Mutation`) require the X-CSRFToken
 * injection layer that slice 6 will wire into `frontend/src/proxy.ts`
 * (DECISIONS slice 2 fork 4). They are deliberately NOT re-exported here
 * because calling any of them today would 403 against Django's CSRF check
 * (Codex slice 2 round 6, 2026-05-27).
 *
 * If a consumer genuinely needs a write helper before slice 6, import
 * directly from `@/lib/api/generated/sdk.gen` — the explicit reach into the
 * gen tree makes the unsafe-without-CSRF status visible at the call site.
 *
 * When slice 6 wires CSRF, REVERT this file to:
 *
 *     export * from "./generated";
 *     export { client } from "./generated/client.gen";
 *
 * That's the one-commit unwiring; remove this comment block too.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Consumers `import { foo } from "@/lib/api"`; the generated path stays an
 * implementation detail of the codegen target.
 */

// ─── Types (safe; typing doesn't fire requests, including types for writes
//      so a consumer reaching into ./generated/sdk.gen for slice 6 prep has
//      everything they need) ─────────────────────────────────────────────
export type * from "./generated/types.gen";

// ─── SDK functions: READ operations only ──────────────────────────────────
// Writes (`importsRowsApproveCreate`, `importsRowsOverrideCreate`,
// `importsRowsRejectCreate`, plus the `Options` shape they share) are
// deliberately omitted; same posture for the TanStack mutation helpers
// below. `type Options` is shared between read and write SDK functions and
// is type-only, so it's safe to re-export here.
export {
  cardsCardsList,
  cardsCardsRetrieve,
  cardsPrintingsList,
  cardsPrintingsRetrieve,
  collectionItemsList,
  collectionItemsRetrieve,
  collectionLotsList,
  collectionLotsRetrieve,
  healthRetrieve,
  importsBatchesList,
  importsBatchesRetrieve,
  importsRowsList,
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

// ─── TanStack Query helpers: READ operations, page-number form only ───────
// Each read SDK function generates {Name}Options + {Name}QueryKey (the
// queryOptions form for `useQuery`) — page-number pagination via
// `options.query.page`, which is what DRF's PageNumberPagination serves.
//
// The {Name}InfiniteOptions + {Name}InfiniteQueryKey variants (for
// `useInfiniteQuery`) are deliberately NOT re-exported (Codex slice 2
// round 7, 2026-05-27): hey-api generates them with only `queryFn`/`queryKey`
// and suppresses TanStack v5's REQUIRED `initialPageParam` + `getNextPageParam`
// with `// @ts-ignore`. They compile at the call site (the suppression is
// inside the generated file, and `infiniteQueryOptions<T>()` returns a
// "complete"-typed object), then can't paginate past page 1 at runtime —
// `getNextPageParam` is undefined so `hasNextPage` is always false. A UI that
// genuinely wants infinite scroll must add a project-owned wrapper that derives
// `getNextPageParam` from DRF's `next` field; that's a slice-3+ UX decision
// (page-number navigation via the regular *Options helpers + `placeholderData:
// keepPreviousData` is the likely TanStack-Table pattern, not infinite scroll).
//
// Mutation helpers (`*Mutation`) for the three import writes are also
// deliberately omitted — same CSRF reason as the SDK writes above.
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
  healthRetrieveOptions,
  healthRetrieveQueryKey,
  importsBatchesListOptions,
  importsBatchesListQueryKey,
  importsBatchesRetrieveOptions,
  importsBatchesRetrieveQueryKey,
  importsRowsListOptions,
  importsRowsListQueryKey,
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
