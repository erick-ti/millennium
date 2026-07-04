/**
 * A column-count-aware loading skeleton for the read-API tables (collection,
 * cards, …). `role="status"` + `aria-busy` announces the loading state to
 * assistive tech on the initial fetch; page-turn loading is announced by the
 * `PaginationControls` live region instead.
 */
export function TableSkeleton({
  columnCount,
  label = "Loading",
}: {
  columnCount: number;
  /** Accessible name for the busy region, e.g. "Loading cards". */
  label?: string;
}) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={label}
      className="overflow-hidden rounded-lg border border-border"
    >
      <div className="h-10 border-b border-border bg-gold-900/10" />
      <div className="divide-y divide-border">
        {Array.from({ length: 8 }).map((_, rowIndex) => (
          <div key={rowIndex} className="flex gap-3 px-3 py-2.5">
            {Array.from({ length: columnCount }).map((_, cellIndex) => (
              <div
                key={cellIndex}
                className="h-4 flex-1 animate-pulse rounded bg-gold-900/15"
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
