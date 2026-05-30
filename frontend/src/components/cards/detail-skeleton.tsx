/**
 * The card-detail loading skeleton, shared by the route's `loading.tsx`
 * (route-shell transition) and the island's `useQuery` pending branch (data
 * fetch) so the two can't drift (review K4, 2026-05-29). The `sr-only` text
 * child gives the `role="status"` live region real content to announce — an
 * empty live region with only `aria-label` may announce nothing on insertion
 * (review C4).
 */
export function DetailSkeleton() {
  return (
    <div
      className="mx-auto max-w-6xl px-6 py-10"
      role="status"
      aria-busy="true"
      aria-label="Loading card"
    >
      <span className="sr-only">Loading card…</span>
      <div className="h-8 w-72 animate-pulse rounded bg-muted" />
      <div className="mt-3 h-4 w-40 animate-pulse rounded bg-muted" />
      <div className="mt-8 h-48 animate-pulse rounded-lg border border-border bg-muted/20" />
      <div className="mt-8 h-72 animate-pulse rounded-lg border border-border bg-muted/20" />
    </div>
  );
}
