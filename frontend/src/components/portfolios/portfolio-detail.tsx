"use client";

import { useMemo } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  type PortfolioValueSnapshot,
  portfolioPortfoliosRetrieveOptions,
  portfolioSnapshotsList,
} from "@/lib/api";
import { PageHeader } from "@/components/ui/page-header";
import { QueryErrorState } from "@/components/ui/query-error-state";
import {
  PriceLineChart,
  type PricePoint,
} from "@/components/charts/price-line-chart";
import { DetailSkeleton } from "@/components/cards/detail-skeleton";
import { PortfolioMetrics } from "@/components/portfolios/portfolio-metrics";
import { formatDayShort, parseDecimal } from "@/lib/format";

// DRF paginates at PAGE_SIZE=100 with no page_size override; the full value
// history needs page-walking. Cap well above any real series (50 pages = 5,000
// daily snapshots) as a runaway backstop.
const MAX_HISTORY_PAGES = 50;

type ValueHistory = { snapshots: PortfolioValueSnapshot[]; truncated: boolean };

/**
 * Page-walk every value snapshot for one portfolio, following DRF's `next`
 * link. The list is newest-first; the caller re-sorts ascending for the chart.
 * `truncated` is true if the page cap was hit with more rows still available —
 * surfaced rather than silently dropped (the project's "no silent caps" rule).
 */
async function fetchAllSnapshots(
  portfolio: number,
  signal: AbortSignal,
): Promise<ValueHistory> {
  const snapshots: PortfolioValueSnapshot[] = [];
  let truncated = false;
  for (let page = 1; page <= MAX_HISTORY_PAGES; page += 1) {
    const { data } = await portfolioSnapshotsList({
      query: { portfolio, page },
      signal,
      throwOnError: true,
    });
    snapshots.push(...(data?.results ?? []));
    if (!data?.next) {
      break;
    }
    if (page === MAX_HISTORY_PAGES) {
      truncated = true;
    }
  }
  return { snapshots, truncated };
}

export function PortfolioDetail({ portfolioId }: { portfolioId: number }) {
  const portfolioQuery = useQuery(
    portfolioPortfoliosRetrieveOptions({ path: { id: portfolioId } }),
  );

  const historyQuery = useQuery({
    queryKey: ["portfolioValueHistory", portfolioId],
    queryFn: ({ signal }) => fetchAllSnapshots(portfolioId, signal),
    placeholderData: keepPreviousData,
    staleTime: 60 * 1000,
  });
  const snapshots = useMemo(
    () => historyQuery.data?.snapshots ?? [],
    [historyQuery.data],
  );
  const historyTruncated = historyQuery.data?.truncated ?? false;

  // Chart series: market_value over snapshot_date, ascending. market_value is
  // non-null on the API, but a malformed decimal is a gap (drop the point), not
  // a zero — same rule as the slice-4 price chart. We carry market_value_complete
  // per point: on a partial-coverage day market_value sums only the PRICED
  // subset (DECISIONS 2026-05-25 slice-4a), so a coverage dip would otherwise
  // read as a real value drop. Partial points are marked + labeled by the chart.
  const series: PricePoint[] = useMemo(
    () =>
      snapshots
        .map((snap) => ({
          date: snap.snapshot_date,
          price: parseDecimal(snap.market_value),
          complete: snap.market_value_complete,
        }))
        .filter(
          (point): point is { date: string; price: number; complete: boolean } =>
            point.price != null,
        )
        .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0)),
    [snapshots],
  );

  const partialCount = useMemo(
    () => series.filter((point) => point.complete === false).length,
    [series],
  );

  if (portfolioQuery.isPending) {
    return <DetailSkeleton label="portfolio" />;
  }

  if (portfolioQuery.isError || portfolioQuery.data == null) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-10">
        <BackLink />
        <div className="mt-4">
          <QueryErrorState
            title="Couldn't load this portfolio."
            onRetry={() => portfolioQuery.refetch()}
          />
        </div>
      </div>
    );
  }

  const portfolio = portfolioQuery.data;
  const latest = portfolio.latest_snapshot;
  const chartLabel =
    series.length > 0
      ? `${portfolio.name} value, ${formatDayShort(series[0].date)} to ${formatDayShort(
          series[series.length - 1].date,
        )}, ${series.length} points`
      : "Value history";

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <BackLink />
      <PageHeader
        className="mt-3"
        kicker="PORTFOLIO"
        title={portfolio.name}
        subtitle={
          latest != null
            ? `Valued ${formatDayShort(latest.snapshot_date)}`
            : "Not yet valued"
        }
      />

      <section>
        <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
          Summary
        </h2>
        <div className="vitrine mt-3 max-w-sm rounded-lg p-5">
          <PortfolioMetrics snapshot={latest} />
        </div>
      </section>

      <section className="mt-10">
        <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
          Value history
        </h2>
        <div className="mt-4">
          {historyQuery.isPending ? (
            <div
              role="status"
              aria-busy="true"
              aria-label="Loading value history"
              className="vitrine h-72 animate-pulse rounded-lg"
            >
              <span className="sr-only">Loading value history…</span>
            </div>
          ) : historyQuery.isError ? (
            <QueryErrorState
              title="Couldn't load value history."
              onRetry={() => historyQuery.refetch()}
            />
          ) : series.length === 0 ? (
            <p className="vitrine rounded-lg p-6 text-sm text-bone-muted">
              No value history for this portfolio yet. Snapshots are recorded by
              the daily valuation run.
            </p>
          ) : (
            <div
              aria-busy={historyQuery.isPlaceholderData}
              className={`vitrine rounded-lg p-4 sm:p-6 ${
                historyQuery.isPlaceholderData
                  ? "opacity-60 transition-opacity"
                  : ""
              }`}
            >
              <PriceLineChart
                data={series}
                label={chartLabel}
                seriesLabel="Portfolio value"
              />
              <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 font-terminal text-[0.7rem] text-bone-muted">
                <span className="flex items-center gap-2">
                  <span className="inline-block h-px w-5 bg-gold-700" />
                  Portfolio value
                </span>
                <span className="flex items-center gap-2">
                  <span className="inline-block size-2 rounded-full border border-flat" />
                  Partial coverage
                </span>
                <span className="ml-auto hidden sm:inline">
                  Tab to the chart for a screen-reader data table.
                </span>
              </div>
              {partialCount > 0 ? (
                <p className="mt-2 font-terminal text-[0.7rem] text-bone-muted">
                  {partialCount === 1
                    ? "1 snapshot had partial pricing coverage and is marked on the chart; its value reflects only the priced cards, not the whole portfolio."
                    : `${partialCount} snapshots had partial pricing coverage and are marked on the chart; their values reflect only the priced cards, not the whole portfolio.`}
                </p>
              ) : null}
              {historyTruncated ? (
                <p className="mt-2 font-terminal text-[0.7rem] text-bone-muted">
                  Showing the most recent snapshots; older history was
                  truncated.
                </p>
              ) : null}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/portfolios"
      className="font-terminal text-xs uppercase tracking-[0.12em] text-gold-700 transition-colors hover:text-gold-500"
    >
      ← Portfolios
    </Link>
  );
}
