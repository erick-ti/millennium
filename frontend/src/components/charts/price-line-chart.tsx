"use client";

import { type ReactElement, useId } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
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
    <circle key={key} cx={cx} cy={cy} r={4} fill="none" stroke="#fbbf24" strokeWidth={1.5} />
  );
}

/**
 * A pure price-over-time area chart (gold line + faint gold gradient fill, the
 * landing chart's treatment). The caller fetches snapshots, maps them to
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
  // Unique per instance so two charts on one page never share a <defs> gradient
  // id. useId carries colons (invalid in a CSS selector) — strip them; the SVG
  // `url(#id)` attribute reference works regardless.
  const gradientId = `pchart-${useId().replace(/:/g, "")}`;
  return (
    <figure className="m-0">
      {/* role="img" hides the SVG subtree from AT and exposes only `label`; the
          full series is the sibling sr-only table below. */}
      <div role="img" aria-label={caption} className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            <defs>
              {/* Aged-gold area fill, 0.26 → 0 — the landing chart's treatment,
                  so the authed series reads as the same instrument. */}
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#c8a24a" stopOpacity={0.26} />
                <stop offset="100%" stopColor="#c8a24a" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgba(200,162,74,0.09)" vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={formatDayShort}
              minTickGap={24}
              tick={{ fontSize: 12, fill: "#b8b09e", fontFamily: "var(--font-terminal)" }}
              tickLine={false}
              axisLine={{ stroke: "rgba(200,162,74,0.25)" }}
            />
            <YAxis
              width={72}
              tickFormatter={(value) => formatUsd(Number(value))}
              domain={["auto", "auto"]}
              tick={{ fontSize: 12, fill: "#b8b09e", fontFamily: "var(--font-terminal)" }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ stroke: "rgba(200,162,74,0.35)", strokeDasharray: "3 3" }}
              contentStyle={{
                background: "oklch(0.17 0.008 70)",
                border: "1px solid rgba(200,162,74,0.35)",
                borderRadius: 8,
                fontFamily: "var(--font-terminal)",
                fontSize: 12,
                color: "#f5f1e6",
              }}
              labelStyle={{ color: "#b8b09e" }}
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
            <Area
              type="monotone"
              dataKey="price"
              // Aged-gold line + gradient fill on the vault canvas (the landing
              // chart's exact treatment).
              stroke="#c8a24a"
              strokeWidth={1.75}
              fill={`url(#${gradientId})`}
              activeDot={{ r: 3.5, fill: "#e6c063", stroke: "none" }}
              // Marker only on partial-coverage points (otherwise no dot);
              // see renderCoverageDot.
              dot={renderCoverageDot}
              // Deterministic paint — no animation timers to await under jsdom,
              // and no flicker when the series changes on a refetch.
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {/* sr-only on the WRAPPER div, not the <table>: a table with
          table-layout:auto ignores width:1px and grows to its content, which
          leaks horizontal page overflow on narrow viewports — the div honors
          width:1px + overflow:hidden and clips it. */}
      <div className="sr-only">
        <table>
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
      </div>
    </figure>
  );
}
