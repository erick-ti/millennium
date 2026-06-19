"use client";

import { Button } from "@/components/ui/button";

// An anonymous 403 now redirects to /login globally (the auth slice), so this
// panel is for network / transient failures — with a sign-in mention as the
// fallback wording for an expired session. Shared so the copy lives in one place.
const DEFAULT_DESCRIPTION =
  "This usually means the server is unreachable or your session expired. Try again, or sign in if you were signed out.";

interface QueryErrorStateProps {
  /** The headline, e.g. "Couldn't load your collection." */
  title: string;
  /** Defaults to the not-signed-in/unreachable explanation. */
  description?: string;
  onRetry: () => void;
  /**
   * Optional "back to a known-good page" escape for paged lists —
   * `keepPreviousData` drops the kept page on error, so a failure on page >1
   * would otherwise strand the user on a dead-end card (DECISIONS 2026-05-29).
   * Omit both for non-paginated views.
   */
  backLabel?: string;
  onBack?: () => void;
}

/**
 * The shared read-API load-error panel: a destructive-bordered card with a
 * Retry and an optional paged-escape button. Extracted from the slice-3
 * collection view (mirroring how `PaginationControls` / `TableSkeleton` were
 * centralized) so the cards list, card detail, and collection views don't carry
 * three drifting copies (review C5, 2026-05-29).
 */
export function QueryErrorState({
  title,
  description = DEFAULT_DESCRIPTION,
  onRetry,
  backLabel,
  onBack,
}: QueryErrorStateProps) {
  return (
    <div className="rounded-lg border border-loss/30 bg-loss/[0.06] p-6">
      <p className="font-terminal text-[0.7rem] font-medium uppercase tracking-[0.2em] text-loss">
        Error
      </p>
      <p className="mt-2 font-display text-lg font-semibold text-bone">{title}</p>
      <p className="mt-1.5 max-w-md font-body text-sm leading-relaxed text-bone-muted">
        {description}
      </p>
      <div className="mt-4 flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
        {onBack && backLabel ? (
          <Button variant="ghost" size="sm" onClick={onBack}>
            {backLabel}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
