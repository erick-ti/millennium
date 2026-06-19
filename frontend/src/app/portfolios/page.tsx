"use client";

import { useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { portfolioPortfoliosListOptions } from "@/lib/api";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { PortfolioSummaryCard } from "@/components/portfolios/portfolio-summary-card";
import { QueryErrorState } from "@/components/ui/query-error-state";

// DRF serves one fixed page size globally (PageNumberPagination, PAGE_SIZE=100).
// Portfolio counts are single-digit in practice, so this is one page — but
// Prev/Next enablement still reads the authoritative `next`/`previous` links,
// never arithmetic, so a backend page-size change degrades only the cosmetic
// "page X of Y", never navigation correctness.
const PAGE_SIZE = 100;

// Stable keys for the loading-skeleton cards (index keys trip the lint rule and
// the count is fixed anyway).
const SKELETON_KEYS = ["s1", "s2", "s3"];

export default function PortfoliosPage() {
  const [page, setPage] = useState(1);

  const query = useQuery({
    ...portfolioPortfoliosListOptions({ query: { page } }),
    // Keep the current page visible while the next one loads (no flash to a
    // skeleton on every page change). Page-number pattern, same as slices 3/4.
    placeholderData: keepPreviousData,
  });

  const data = query.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = query.isPlaceholderData;
  const hasPrev = Boolean(data?.previous);
  const hasNext = Boolean(data?.next);
  const portfolios = data?.results ?? [];

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <PageHeader
        kicker="VALUATION"
        title="Portfolios"
        subtitle="Your collection, grouped and valued like investment portfolios."
      />

      <div className="mt-6">
        {query.isPending ? (
          <div
            role="status"
            aria-busy="true"
            aria-label="Loading portfolios"
            className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
          >
            <span className="sr-only">Loading portfolios…</span>
            {SKELETON_KEYS.map((key) => (
              <div
                key={key}
                className="vitrine h-44 animate-pulse rounded-lg"
              />
            ))}
          </div>
        ) : query.isError ? (
          <QueryErrorState
            title="Couldn't load your portfolios."
            onRetry={() => query.refetch()}
            // keepPreviousData drops the kept page on error, so a failure on
            // page >1 would otherwise strand the user on a dead-end view.
            backLabel={page > 1 ? `Back to page ${page - 1}` : undefined}
            onBack={
              page > 1
                ? () => setPage((current) => Math.max(1, current - 1))
                : undefined
            }
          />
        ) : portfolios.length === 0 ? (
          <EmptyState
            title="No portfolios yet"
            description="Import a collection and your holdings are grouped into portfolios — each one valued daily like an investment position."
            action={
              <Link
                href="/imports"
                className="inline-flex items-center gap-1.5 rounded-lg border border-gold-700/50 bg-gold-700/10 px-4 py-2 font-terminal text-xs uppercase tracking-[0.12em] text-gold-300 transition-colors hover:bg-gold-700/20"
              >
                Import a CSV →
              </Link>
            }
          />
        ) : (
          <div
            aria-busy={isPaging}
            className={isPaging ? "opacity-60 transition-opacity" : undefined}
          >
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {portfolios.map((portfolio) => (
                <li key={portfolio.id}>
                  <PortfolioSummaryCard portfolio={portfolio} />
                </li>
              ))}
            </ul>

            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="portfolio"
              isPaging={isPaging}
              hasPrev={hasPrev}
              hasNext={hasNext}
              onPageChange={setPage}
            />
          </div>
        )}
      </div>
    </div>
  );
}
