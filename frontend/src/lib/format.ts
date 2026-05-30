/**
 * Shared display formatters.
 *
 * Currency and dates are formatted in UTC to match the backend
 * (`USE_TZ=True`, `TIME_ZONE="UTC"`). `price_snapshots.snapshot_date` is an
 * append-only natural-key field, so formatting a bare date in OS-local time
 * could shift a label across midnight and mislabel the daily series — the same
 * "use localdate, never naive local time" rule the backend follows.
 */

// One module-scope Intl formatter (stable identity, no per-render allocation,
// SSR-safe — no DOM access).
const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

/** Format a numeric USD amount, e.g. `42.1` → `"$42.10"`. */
export function formatUsd(value: number): string {
  return usd.format(value);
}

/**
 * Parse a DRF decimal string (e.g. `"42.10"`) to a number, or `null` for a
 * missing price. NEVER coerce `null`/`""` to `0` — a missing price is a gap,
 * not zero (the fake-zero avoidance the backend models with nullable price
 * fields). Returns `null` on a non-numeric value too.
 */
export function parseDecimal(value: string | null | undefined): number | null {
  if (value == null || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * Format an ISO `"YYYY-MM-DD"` date as a short label, e.g. `"May 12"`. Parsed
 * and rendered in UTC so a bare date can't drift a day in a non-UTC locale.
 */
export function formatDayShort(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
