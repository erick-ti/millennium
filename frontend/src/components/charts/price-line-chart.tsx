"use client";

import type { ReactElement } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDayShort, formatUsd } from "@/lib/format";

export type PricePoint = {
  /** ISO `"YYYY-MM-DD"` (from `price_snapshots.snapshot_date`). */
  date: string;
  /** Numeric USD market price (the caller parses the DRF decimal string). */
  price: number;
  /**
   * Coverage flag for an AGGREGATE series (e.g. a portfolio's daily value):
   * `false` marks a point whose total covers only a subset of holdings (partial
   * pricing coverage), so it is NOT comparable to complete points and gets a
   * visible marker + a "partial coverage" label. `undefined` — the default,
   * e.g. a single card's price series — means there is no coverage dimension and
   * the point renders normally.
   */
  complete?: boolean;
};

// Recharts dot renderer: draw a marker ONLY on partial-coverage points, so a
// coverage-driven dip (fewer cards priced that day) is visually distinguishable
// from a real value move. Complete or coverage-agnostic points render no dot —
// matching the prior `dot={false}` behavior for the card price series.
function renderCoverageDot(props: {
  cx?: number;
  cy?: number;
  index?: number;
  payload?: PricePoint;
}): ReactElement {
  const { cx, cy, index, payload } = props;
  const key = `dot-${index ?? 0}`;
  if (
    payload?.complete !== false ||
    cx == null ||
    cy == null ||
    !Number.isFinite(cx) ||
    !Number.isFinite(cy)
  ) {
    return <g key={key} />;
  }
  return (
    <circle
      key={key}
      cx={cx}
      cy={cy}
      r={3.5}
      fill="#f59e0b"
      stroke="white"
      strokeWidth={1}
    />
  );
}

/**
 * A pure price-over-time line chart. The caller fetches snapshots, maps them to
 * an ascending `PricePoint[]`, and owns loading / empty / error — this stays a
 * props-in, chart-out component so it's trivially testable (mock
 * `ResponsiveContainer` to a fixed size under jsdom; DECISIONS 2026-05-29
 * slice 4).
 *
 * Accessibility (review C3): Recharts emits an `aria-hidden` SVG, so the series
 * would otherwise be invisible to assistive tech — and the date/price history
 * lives ONLY in this chart (the printings table shows a single latest price).
 * So the chart div carries `role="img"` + a summary `label`, and a sibling
 * `sr-only` table exposes every point's date + price as real, readable text.
 */
export function PriceLineChart({
  data,
  label,
  seriesLabel = "Market price",
}: {
  data: PricePoint[];
  /** A concise summary used as the chart's accessible name + table caption. */
  label?: string;
  /**
   * Name of the plotted series — used for the tooltip value label and the
   * `sr-only` column header. Defaults to "Market price" (the slice-4 card-price
   * chart); the portfolio value-history chart (slice 5) passes "Portfolio value"
   * so this pure component can plot a non-price USD series without a mislabeled
   * tooltip.
   */
  seriesLabel?: string;
}) {
  const caption = label ?? "Price history";
  // Only an aggregate (coverage-carrying) series turns on the coverage column;
  // a coverage-agnostic series (card prices) keeps the original 2-column table.
  const hasCoverage = data.some((point) => point.complete !== undefined);
  return (
    <figure className="m-0">
      {/* role="img" hides the SVG subtree from AT and exposes only `label`; the
          full series is the sibling sr-only table below. */}
      <div role="img" aria-label={caption} className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis
              dataKey="date"
              tickFormatter={formatDayShort}
              minTickGap={24}
              tick={{ fontSize: 12 }}
            />
            <YAxis
              width={72}
              tickFormatter={(value) => formatUsd(Number(value))}
              domain={["auto", "auto"]}
              tick={{ fontSize: 12 }}
            />
            <Tooltip
              formatter={(value, _name, item) => {
                const point = item?.payload as PricePoint | undefined;
                const name =
                  point?.complete === false
                    ? `${seriesLabel} (partial coverage)`
                    : seriesLabel;
                return [formatUsd(Number(value)), name];
              }}
              labelFormatter={(value) => formatDayShort(String(value))}
            />
            <Line
              type="monotone"
              dataKey="price"
              // A fixed, always-visible color: a CSS-var stroke that fails to
              // resolve would render an invisible line. Theme polish later.
              stroke="#2563eb"
              strokeWidth={2}
              // Marker only on partial-coverage points (otherwise no dot);
              // see renderCoverageDot.
              dot={renderCoverageDot}
              // Deterministic paint — no animation timers to await under jsdom,
              // and no flicker when the series changes on a refetch.
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <table className="sr-only">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">{seriesLabel}</th>
            {hasCoverage ? <th scope="col">Coverage</th> : null}
          </tr>
        </thead>
        <tbody>
          {data.map((point) => (
            <tr key={point.date}>
              <td>{point.date}</td>
              <td>{formatUsd(point.price)}</td>
              {hasCoverage ? (
                <td>
                  {point.complete === false ? "Partial coverage" : "Complete"}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
