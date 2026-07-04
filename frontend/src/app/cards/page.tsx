"use client";

import { useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import {
  type CardList,
  cardsCardsArchetypesRetrieveOptions,
  cardsCardsListOptions,
} from "@/lib/api";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/page-header";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";

// DRF serves one fixed page size globally (PageNumberPagination, PAGE_SIZE=100).
// Used only for the cosmetic "page X of Y"; Prev/Next enablement is driven by
// the authoritative `next`/`previous` links.
const PAGE_SIZE = 100;

const columns: Array<ColumnDef<CardList>> = [
  {
    accessorKey: "name",
    header: "Card",
    cell: ({ row }) => (
      <Link
        href={`/cards/${row.original.id}`}
        className="font-medium text-gold-300 underline-offset-4 transition-colors hover:text-gold-500 hover:underline"
      >
        {row.original.name}
      </Link>
    ),
  },
  {
    accessorKey: "archetype",
    header: "Archetype",
    // ~40% of cards have no archetype (NULL), render an em-dash, never "".
    cell: ({ row }) =>
      row.original.archetype ?? <span className="text-bone-muted">—</span>,
  },
  {
    accessorKey: "printings_count",
    header: () => <div className="text-right">Printings</div>,
    cell: ({ row }) => (
      <div className="text-right nums-terminal">
        {row.original.printings_count}
      </div>
    ),
  },
];

export default function CardsPage() {
  const [page, setPage] = useState(1);
  const [archetype, setArchetype] = useState<string | null>(null);

  const archetypesQuery = useQuery({
    ...cardsCardsArchetypesRetrieveOptions(),
    // Archetypes only change on a metadata sync and the list is small (a few
    // hundred at most); don't refetch as the user pages the table.
    staleTime: 5 * 60 * 1000,
  });
  const archetypes = archetypesQuery.data ?? [];

  const cardsQuery = useQuery({
    ...cardsCardsListOptions({
      query: { page, ...(archetype !== null ? { archetype } : {}) },
    }),
    // Keep the current page visible while the next one loads (no flash to a
    // spinner on every page/filter change).
    placeholderData: keepPreviousData,
  });

  const data = cardsQuery.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = cardsQuery.isPlaceholderData;
  const hasPrev = Boolean(data?.previous);
  const hasNext = Boolean(data?.next);

  const emptyMessage =
    archetype !== null
      ? "No cards in this archetype."
      : "No cards yet. Sync the catalog to populate it.";

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <PageHeader
        kicker="CATALOG"
        title="Cards"
        subtitle="The full Yu-Gi-Oh catalog: search, filter, and chart any printing."
      />

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-bone-muted">
          <span>Archetype</span>
          <select
            aria-label="Filter by archetype"
            value={archetype ?? ""}
            disabled={archetypesQuery.isPending || archetypes.length === 0}
            onChange={(event) => {
              const value = event.target.value;
              setArchetype(value === "" ? null : value);
              setPage(1);
            }}
            className="h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          >
            <option value="">All archetypes</option>
            {archetypes.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
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
              emptyMessage={emptyMessage}
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
