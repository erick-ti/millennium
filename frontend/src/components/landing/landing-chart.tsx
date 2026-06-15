"use client";

import type { ReactElement } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CURVE } from "@/components/landing/data";
import { formatDayShort, formatUsd } from "@/lib/format";

// Amber hollow ring ONLY on a partial-coverage day, so a pricing gap reads as
// "partial," not a value crash. Other points draw no dot.
function CoverageDot(props: {
  cx?: number;
  cy?: number;
  index?: number;
  payload?: (typeof CURVE)[number];
}): ReactElement {
  const { cx, cy, index, payload } = props;
  const key = `dot-${index ?? 0}`;
  if (payload?.complete !== false || cx == null || cy == null) {
    return <g key={key} />;
  }
  return (
    <circle key={key} cx={cx} cy={cy} r={4} fill="none" stroke="#fbbf24" strokeWidth={1.5} />
  );
}

/**
 * The Curve — a gold-on-dark portfolio value series. A razor-thin gold line
 * over a faint amber gradient fill (never Tailwind-blue), with partial-coverage
 * days marked amber. A sibling data table mirrors every point and becomes
 * visible on focus, so the accessibility claim is demonstrable, not asserted.
 */
export function LandingChart() {
  return (
    <figure className="group m-0">
      <div role="img" aria-label="Portfolio value over time" className="h-72 w-full sm:h-80">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={CURVE as unknown as (typeof CURVE)[number][]} margin={{ top: 10, right: 12, bottom: 4, left: 6 }}>
            <defs>
              <linearGradient id="goldFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#c8a24a" stopOpacity={0.26} />
                <stop offset="100%" stopColor="#c8a24a" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgba(200,162,74,0.09)" vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={formatDayShort}
              minTickGap={36}
              tick={{ fontSize: 11, fill: "#b8b09e", fontFamily: "var(--font-terminal)" }}
              tickLine={false}
              axisLine={{ stroke: "rgba(200,162,74,0.25)" }}
            />
            <YAxis
              width={64}
              tickFormatter={(value) => formatUsd(Number(value))}
              domain={["auto", "auto"]}
              tick={{ fontSize: 11, fill: "#b8b09e", fontFamily: "var(--font-terminal)" }}
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
                const point = item?.payload as (typeof CURVE)[number] | undefined;
                const name =
                  point?.complete === false ? "Portfolio value (partial coverage)" : "Portfolio value";
                return [formatUsd(Number(value)), name];
              }}
              labelFormatter={(value) => formatDayShort(String(value))}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke="#c8a24a"
              strokeWidth={1.75}
              fill="url(#goldFill)"
              dot={CoverageDot}
              activeDot={{ r: 3.5, fill: "#e6c063", stroke: "none" }}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* a11y mirror — visible on keyboard focus so a sighted reviewer sees it */}
      <table
        tabIndex={0}
        className="sr-only mt-4 w-full rounded-md border border-gold-900/20 font-terminal text-sm focus:not-sr-only focus:block"
      >
        <caption className="px-3 pt-3 text-left text-bone-muted">
          Portfolio value by date — the chart&rsquo;s data, as a table.
        </caption>
        <thead>
          <tr className="text-left text-bone-muted">
            <th scope="col" className="px-3 py-1.5">Date</th>
            <th scope="col" className="px-3 py-1.5">Value</th>
            <th scope="col" className="px-3 py-1.5">Coverage</th>
          </tr>
        </thead>
        <tbody className="nums-terminal">
          {CURVE.map((point) => (
            <tr key={point.date}>
              <td className="px-3 py-1">{point.date}</td>
              <td className="px-3 py-1">{formatUsd(point.value)}</td>
              <td className="px-3 py-1">{point.complete ? "Complete" : "Partial"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
