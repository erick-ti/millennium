"use client";

import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import {
  type CollectionItemList,
  type ConditionEnum,
  type EditionEnum,
  type LanguageEnum,
  collectionItemsListOptions,
  portfolioPortfoliosListOptions,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";

// DRF serves one fixed page size globally (PageNumberPagination, PAGE_SIZE=100).
// Used only to render "page X of Y" — Prev/Next enablement is driven by the
// authoritative `next`/`previous` links the API returns, so a backend page-size
// change degrades only the cosmetic page count, never navigation correctness.
const PAGE_SIZE = 100;

const CONDITION_LABELS: Record<ConditionEnum, string> = {
  mint: "Mint",
  near_mint: "Near Mint",
  excellent: "Excellent",
  good: "Good",
  light_played: "Light Played",
  played: "Played",
  poor: "Poor",
};

const EDITION_LABELS: Record<EditionEnum, string> = {
  first: "1st",
  unlimited: "Unlimited",
  limited: "Limited",
};

const LANGUAGE_LABELS: Record<LanguageEnum, string> = {
  en: "EN",
  fr: "FR",
  de: "DE",
  it: "IT",
  es: "ES",
  pt: "PT",
  ja: "JA",
  ko: "KO",
};

const columns: Array<ColumnDef<CollectionItemList>> = [
  { accessorKey: "card_name", header: "Card" },
  { accessorKey: "set_code", header: "Set" },
  { accessorKey: "set_rarity", header: "Rarity" },
  {
    accessorKey: "variant_label",
    header: "Variant",
    cell: ({ row }) => row.original.variant_label ?? "—",
  },
  {
    accessorKey: "condition",
    header: "Condition",
    cell: ({ row }) => CONDITION_LABELS[row.original.condition],
  },
  {
    accessorKey: "edition",
    header: "Edition",
    cell: ({ row }) => EDITION_LABELS[row.original.edition],
  },
  {
    accessorKey: "language",
    header: "Lang",
    cell: ({ row }) => LANGUAGE_LABELS[row.original.language],
  },
  {
    accessorKey: "quantity",
    header: () => <div className="text-right">Qty</div>,
    cell: ({ row }) => (
      <div className="text-right tabular-nums">{row.original.quantity}</div>
    ),
  },
  { accessorKey: "portfolio_name", header: "Portfolio" },
];

export default function CollectionPage() {
  const [page, setPage] = useState(1);
  const [portfolioId, setPortfolioId] = useState<number | null>(null);

  const portfoliosQuery = useQuery({
    ...portfolioPortfoliosListOptions(),
    // Portfolios change rarely and the list is small (single digits); don't
    // thrash the proxy refetching them as the user pages the table.
    staleTime: 5 * 60 * 1000,
  });
  const portfolios = portfoliosQuery.data?.results ?? [];

  const itemsQuery = useQuery({
    ...collectionItemsListOptions({
      query: {
        page,
        ...(portfolioId !== null ? { portfolio: portfolioId } : {}),
      },
    }),
    // Keep the current page visible while the next one loads (no flash to a
    // spinner on every page/filter change). Infinite-scroll helpers are
    // deliberately unavailable; this is the page-number pattern.
    placeholderData: keepPreviousData,
  });

  const data = itemsQuery.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = itemsQuery.isPlaceholderData;
  const hasPrev = Boolean(data?.previous);
  const hasNext = Boolean(data?.next);

  const emptyMessage =
    portfolioId !== null
      ? "No holdings in this portfolio."
      : "No holdings yet. Import a collection to get started.";

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Collection</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every holding across your portfolios.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>Portfolio</span>
          <select
            aria-label="Filter by portfolio"
            value={portfolioId ?? ""}
            disabled={portfoliosQuery.isPending}
            onChange={(event) => {
              const value = event.target.value;
              setPortfolioId(value === "" ? null : Number(value));
              setPage(1);
            }}
            className="h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          >
            <option value="">All portfolios</option>
            {portfolios.map((portfolio) => (
              <option key={portfolio.id} value={portfolio.id}>
                {portfolio.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-6">
        {itemsQuery.isPending ? (
          <TableSkeleton columnCount={columns.length} />
        ) : itemsQuery.isError ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-6">
            <p className="text-sm font-medium text-destructive">
              Couldn&apos;t load your collection.
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              This usually means you aren&apos;t signed in (sign-in lands in a
              later slice), but it can also happen if the server is unreachable.
            </p>
            <div className="mt-3 flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => itemsQuery.refetch()}
              >
                Retry
              </Button>
              {/* keepPreviousData drops the kept page on error, so a failure on
                  page >1 would otherwise strand the user on a dead-end card.
                  Give them a way back to a page known to exist. */}
              {page > 1 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  Back to page {page - 1}
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div
            aria-busy={isPaging}
            className={isPaging ? "opacity-60 transition-opacity" : undefined}
          >
            <DataTable
              columns={columns}
              data={data?.results ?? []}
              emptyMessage={emptyMessage}
            />

            {count > 0 && (
              <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
                {/* Live region so the new page is announced after a turn — the
                    skeleton's role="status" only fires on the initial load,
                    not on keepPreviousData page changes. */}
                <span role="status" aria-live="polite">
                  Page {page} of {totalPages} · {count}{" "}
                  {count === 1 ? "item" : "items"}
                </span>
                <div className="flex items-center gap-2">
                  {/* Disable only at the true boundaries — NOT on isPaging.
                      Disabling the focused button mid-turn would blur focus to
                      <body> (a keyboard-focus trap); the onClick guard instead
                      no-ops a click while a fetch is in flight, preserving both
                      focus and the double-click protection. */}
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!hasPrev}
                    onClick={() => {
                      if (isPaging) return;
                      setPage((current) => Math.max(1, current - 1));
                    }}
                  >
                    Prev
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!hasNext}
                    onClick={() => {
                      if (isPaging) return;
                      setPage((current) => current + 1);
                    }}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function TableSkeleton({ columnCount }: { columnCount: number }) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Loading collection"
      className="overflow-hidden rounded-lg border border-border"
    >
      <div className="h-10 border-b border-border bg-muted/30" />
      <div className="divide-y divide-border">
        {Array.from({ length: 8 }).map((_, rowIndex) => (
          <div key={rowIndex} className="flex gap-3 px-3 py-2.5">
            {Array.from({ length: columnCount }).map((_, cellIndex) => (
              <div
                key={cellIndex}
                className="h-4 flex-1 animate-pulse rounded bg-muted"
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
