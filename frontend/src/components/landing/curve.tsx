import { LandingChart } from "@/components/landing/landing-chart";

const WINDOWS = ["7D", "30D", "90D"] as const;

/**
 * 02 — The Curve. A compositional change of pace: header + window pills on one
 * line, then a broad, chart-dominant panel (the data is the center of gravity,
 * not a framed afterthought). Proves the coverage-aware valuation + the a11y
 * mirror on real-shaped data.
 */
export function Curve() {
  return (
    <section className="relative z-10 border-t border-gold-900/15 bg-vault-950 py-24 text-bone">
      <div className="mx-auto max-w-6xl px-6">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="font-terminal text-xs uppercase tracking-[0.3em] text-gold-900">
              01 — The Curve
            </p>
            <h2 className="mt-3 font-display text-3xl font-semibold leading-tight text-bone sm:text-4xl">
              A portfolio&rsquo;s worth, day by day.
            </h2>
          </div>
          <div
            className="flex gap-1 font-terminal text-xs"
            role="group"
            aria-label="Time window (illustrative)"
          >
            {WINDOWS.map((w) => (
              <span
                key={w}
                className={
                  w === "90D"
                    ? "rounded-sm border border-gold-700/50 bg-gold-700/10 px-2.5 py-1 text-gold-300"
                    : "rounded-sm border border-gold-900/25 px-2.5 py-1 text-bone-muted"
                }
              >
                {w}
              </span>
            ))}
          </div>
        </div>

        <p className="mt-5 max-w-2xl font-body leading-relaxed text-bone-muted">
          Append-only daily value snapshots. On a day where pricing covers only
          part of the collection, the point is marked amber and noted partial —
          a coverage gap never masquerades as a value drop.
        </p>

        <div className="vitrine mt-9 rounded-lg p-4 sm:p-7">
          <LandingChart />
          <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 font-terminal text-[0.7rem] text-bone-muted">
            <span className="flex items-center gap-2">
              <span className="inline-block h-px w-5 bg-gold-700" /> Portfolio value
            </span>
            <span className="flex items-center gap-2">
              <span className="inline-block size-2 rounded-full border border-flat" /> Partial coverage
            </span>
            <span className="ml-auto hidden sm:inline">
              Tab to the chart for a screen-reader data table.
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
