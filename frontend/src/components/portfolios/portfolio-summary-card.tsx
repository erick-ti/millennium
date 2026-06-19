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
    <div className="vitrine h-full rounded-lg p-5">
      <h2 className="font-display text-base font-semibold tracking-tight">
        <Link
          href={`/portfolios/${portfolio.id}`}
          className="text-gold-700 transition-colors hover:text-gold-500"
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
