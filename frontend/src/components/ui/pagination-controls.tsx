"use client";

import { Button } from "@/components/ui/button";

interface PaginationControlsProps {
  /** 1-based current page. */
  page: number;
  /** Total pages (cosmetic "page X of Y", derived from `count`/PAGE_SIZE). */
  totalPages: number;
  /** Total row count across all pages. */
  count: number;
  /** Singular noun for the count, e.g. "card" → "1 card" / "2 cards". */
  noun: string;
  /** True while a page fetch is in flight (keepPreviousData). */
  isPaging: boolean;
  /** Authoritative from the API's `previous`/`next` links. */
  hasPrev: boolean;
  hasNext: boolean;
  /** Called with the requested page when the user navigates. */
  onPageChange: (page: number) => void;
}

/**
 * Page-number Prev/Next + a live page-status region. The server owns
 * pagination, so this is a thin control shared across the collection (slice 3),
 * cards (slice 4), and portfolio (slice 5) views.
 *
 * a11y rules carried from the slice-3 review:
 *  - Disable a button ONLY at the true boundary (`!hasPrev`/`!hasNext`), NEVER
 *    on `isPaging`: disabling the focused button mid-turn blurs focus to
 *    `<body>` (a keyboard-focus trap). The `isPaging` guard in `onClick`
 *    no-ops a click while a fetch is in flight, preserving focus AND the
 *    double-click protection.
 *  - `role="status" aria-live="polite"` announces the new page after a turn
 *    (the skeleton's `role="status"` only fires on the initial load).
 */
export function PaginationControls({
  page,
  totalPages,
  count,
  noun,
  isPaging,
  hasPrev,
  hasNext,
  onPageChange,
}: PaginationControlsProps) {
  if (count === 0) {
    return null;
  }
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-bone-muted">
      <span
        role="status"
        aria-live="polite"
        className="font-terminal nums-terminal text-xs"
      >
        Page {page} of {totalPages} · {count} {count === 1 ? noun : `${noun}s`}
      </span>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!hasPrev}
          onClick={() => {
            if (isPaging) return;
            onPageChange(Math.max(1, page - 1));
          }}
        >
          Prev
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!hasNext}
          onClick={() => {
            if (isPaging) return;
            onPageChange(page + 1);
          }}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
