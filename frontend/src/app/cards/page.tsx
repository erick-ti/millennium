"use client";

import { useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import { type CardList, cardsCardsListOptions } from "@/lib/api";
import { DataTable } from "@/components/ui/data-table";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";

// DRF serves one fixed page size globally (PageNumberPagination, PAGE_SIZE=100).
// Used only for the cosmetic "page X of Y"; Prev/Next enablement is driven by
// the authoritative `next`/`previous` links (DECISIONS 2026-05-29 slice 3).
const PAGE_SIZE = 100;

const columns: Array<ColumnDef<CardList>> = [
  {
    accessorKey: "name",
    header: "Card",
    cell: ({ row }) => (
      <Link
        href={`/cards/${row.original.id}`}
        className="font-medium text-foreground underline-offset-4 hover:underline"
      >
        {row.original.name}
      </Link>
    ),
  },
  {
    accessorKey: "printings_count",
    header: () => <div className="text-right">Printings</div>,
    cell: ({ row }) => (
      <div className="text-right tabular-nums">
        {row.original.printings_count}
      </div>
    ),
  },
];

export default function CardsPage() {
  const [page, setPage] = useState(1);

  const cardsQuery = useQuery({
    ...cardsCardsListOptions({ query: { page } }),
    // Keep the current page visible while the next one loads (no flash to a
    // spinner on every page change). Page-number pattern, per slice 3.
    placeholderData: keepPreviousData,
  });

  const data = cardsQuery.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = cardsQuery.isPlaceholderData;
  const hasPrev = Boolean(data?.previous);
  const hasNext = Boolean(data?.next);

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Cards</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The card catalog — open a card for its printings and price history.
        </p>
      </div>

      <div className="mt-6">
        {cardsQuery.isPending ? (
          <TableSkeleton columnCount={columns.length} label="Loading cards" />
        ) : cardsQuery.isError ? (
          <QueryErrorState
            title="Couldn't load the card catalog."
            onRetry={() => cardsQuery.refetch()}
            // keepPreviousData drops the kept page on error, so a failure on
            // page >1 would otherwise strand the user on a dead-end card.
            backLabel={page > 1 ? `Back to page ${page - 1}` : undefined}
            onBack={
              page > 1
                ? () => setPage((current) => Math.max(1, current - 1))
                : undefined
            }
          />
        ) : (
          <div
            aria-busy={isPaging}
            className={isPaging ? "opacity-60 transition-opacity" : undefined}
          >
            <DataTable
              columns={columns}
              data={data?.results ?? []}
              emptyMessage="No cards yet. Sync the catalog to populate it."
            />
            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="card"
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
