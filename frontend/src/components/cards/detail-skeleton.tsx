/**
 * A generic detail-page loading skeleton (title bar + meta line + two panels),
 * shared by a route's `loading.tsx` (route-shell transition) and the matching
 * island's `useQuery` pending branch so the two can't drift (review K4,
 * 2026-05-29). `label` names the entity being loaded ("card", "portfolio") and
 * feeds BOTH the `aria-label` and the `sr-only` text — the live region needs
 * real text content to announce, not just an `aria-label` (review C4).
 */
export function DetailSkeleton({ label = "card" }: { label?: string } = {}) {
  return (
    <div
      className="mx-auto max-w-6xl px-6 py-10"
      role="status"
      aria-busy="true"
      aria-label={`Loading ${label}`}
    >
      <span className="sr-only">{`Loading ${label}…`}</span>
      <div className="h-8 w-72 animate-pulse rounded bg-muted" />
      <div className="mt-3 h-4 w-40 animate-pulse rounded bg-muted" />
      <div className="mt-8 h-48 animate-pulse rounded-lg border border-border bg-muted/20" />
      <div className="mt-8 h-72 animate-pulse rounded-lg border border-border bg-muted/20" />
    </div>
  );
}
