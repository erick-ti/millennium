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

// Signed currency / percent for deltas (the "biggest movers" view, slice 3).
// `signDisplay: "exceptZero"` prepends an explicit "+" to gains (a loss already
// carries "-"), matching the portfolio gain convention (+$x ▲ / -$x ▼).
const signedUsd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  signDisplay: "exceptZero",
});

/** Format a signed USD delta, e.g. `2.5` → `"+$2.50"`, `-1` → `"-$1.00"`. */
export function formatSignedUsd(value: number): string {
  return signedUsd.format(value);
}

const signedPercent = new Intl.NumberFormat("en-US", {
  style: "percent",
  signDisplay: "exceptZero",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/**
 * Format a fractional change as a signed percent, e.g. `0.125` → `"+12.5%"`.
 * The input is a RATIO (`(end - start) / start`), not an already-scaled percent —
 * `Intl` multiplies by 100. The movers API leaves `pct_change` null for a
 * sub-floor base price; the caller renders that as a sentinel, never `0%`.
 */
export function formatPercent(value: number): string {
  return signedPercent.format(value);
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

// Fixed "en-US" + UTC so the string is identical on the server and the client
// (an SSR/CSR locale or timezone mismatch would trip a hydration error) — the
// status dashboard renders backend UTC timestamps (sync run times, server clock).
const dateTimeUtc = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

/** Format an ISO datetime as a UTC label, e.g. `"Jun 16, 23:45 UTC"`. */
export function formatDateTimeUtc(iso: string): string {
  const date = new Date(iso);
  // A passthrough external string (e.g. a Healthchecks last_ping) could be non-ISO;
  // Intl.format throws RangeError on an Invalid Date, so fall back to the raw string
  // rather than crashing the render.
  return Number.isNaN(date.getTime()) ? iso : `${dateTimeUtc.format(date)} UTC`;
}
