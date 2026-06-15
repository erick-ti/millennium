import type { ReactNode } from "react";

import type { PortfolioValueSnapshot } from "@/lib/api";
import { formatUsd, parseDecimal } from "@/lib/format";

/**
 * Format a DRF decimal-string money field, or `"—"` if it's missing/unparseable.
 * `market_value`/`liquidation_value`/`cost_basis` are non-null on the API, but a
 * malformed value still degrades to a dash rather than `NaN`.
 */
function money(value: string | null | undefined): string {
  const parsed = parseDecimal(value);
  return parsed == null ? "—" : formatUsd(parsed);
}

function MetricRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums text-foreground">{children}</dd>
    </div>
  );
}

/**
 * The coverage-aware metric block for one portfolio's latest valuation, reused
 * by the summary-card grid (`/portfolios`) and the detail header
 * (`/portfolios/[id]`).
 *
 * NULL-safety is the whole point (DECISIONS 2026-05-25 slice 4a):
 *  - `snapshot == null` → the portfolio has never been valued; show a notice,
 *    never a row of `$0.00`s.
 *  - `unrealized_gain == null` → partial coverage, so market_value and
 *    cost_basis sum different subsets and their difference isn't a real gain
 *    (`gain_iff_complete`). Render "partial coverage", NOT `$0.00` — a missing
 *    gain is a gap, not zero. A non-null gain CAN be negative (a real loss).
 */
export function PortfolioMetrics({
  snapshot,
}: {
  snapshot: PortfolioValueSnapshot | null;
}) {
  if (snapshot == null) {
    return (
      <p className="text-sm text-muted-foreground">
        Not yet valued. The daily valuation run records the first snapshot.
      </p>
    );
  }

  const gain = parseDecimal(snapshot.unrealized_gain);
  const total = snapshot.total_card_count;

  return (
    <dl className="space-y-1.5 text-sm">
      <MetricRow label="Market value">{money(snapshot.market_value)}</MetricRow>
      <MetricRow label="Liquidation">
        {money(snapshot.liquidation_value)}
      </MetricRow>
      <MetricRow label="Cost basis">{money(snapshot.cost_basis)}</MetricRow>
      <MetricRow label="Unrealized gain">
        {gain == null ? (
          <span className="text-muted-foreground">
            — <span className="text-xs">(partial coverage)</span>
          </span>
        ) : (
          <span
            className={gain >= 0 ? "text-gain" : "text-loss"}
          >
            {gain >= 0 ? "+" : "-"}
            {formatUsd(Math.abs(gain))} {gain >= 0 ? "▲" : "▼"}
          </span>
        )}
      </MetricRow>
      <MetricRow label="Coverage">
        {total === 0 ? (
          <span className="text-muted-foreground">No cards</span>
        ) : (
          <span className="text-muted-foreground">
            {snapshot.priced_card_count}/{total} priced ·{" "}
            {snapshot.costed_card_count}/{total} costed
          </span>
        )}
      </MetricRow>
    </dl>
  );
}
