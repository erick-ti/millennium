"use client";

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
};

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
}: {
  data: PricePoint[];
  /** A concise summary used as the chart's accessible name + table caption. */
  label?: string;
}) {
  const caption = label ?? "Price history";
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
              formatter={(value) => [formatUsd(Number(value)), "Market price"]}
              labelFormatter={(value) => formatDayShort(String(value))}
            />
            <Line
              type="monotone"
              dataKey="price"
              // A fixed, always-visible color: a CSS-var stroke that fails to
              // resolve would render an invisible line. Theme polish later.
              stroke="#2563eb"
              strokeWidth={2}
              dot={false}
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
            <th scope="col">Market price</th>
          </tr>
        </thead>
        <tbody>
          {data.map((point) => (
            <tr key={point.date}>
              <td>{point.date}</td>
              <td>{formatUsd(point.price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
