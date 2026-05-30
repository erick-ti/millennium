import Link from "next/link";

import type { Portfolio } from "@/lib/api";
import { PortfolioMetrics } from "@/components/portfolios/portfolio-metrics";

/**
 * One portfolio in the `/portfolios` summary grid: a name that links to the
 * drill-in detail route, above the NULL-safe `PortfolioMetrics` block. The list
 * endpoint nests `latest_snapshot` inline, so the grid renders today's value
 * with no per-card round-trip.
 */
export function PortfolioSummaryCard({ portfolio }: { portfolio: Portfolio }) {
  return (
    <div className="h-full rounded-lg border border-border p-4 transition-colors hover:border-foreground/30">
      <h2 className="text-base font-semibold tracking-tight">
        <Link
          href={`/portfolios/${portfolio.id}`}
          className="underline-offset-4 hover:underline"
        >
          {portfolio.name}
        </Link>
      </h2>
      <div className="mt-3">
        <PortfolioMetrics snapshot={portfolio.latest_snapshot} />
      </div>
    </div>
  );
}
