"use client";

import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import { type ErrorGroup, auditErrorGroupsListOptions } from "@/lib/api";
import { DataTable } from "@/components/ui/data-table";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { formatDateTimeUtc } from "@/lib/format";

const PAGE_SIZE = 100;
// Errors are near-live; poll a touch faster than the audit feed.
const REFETCH_MS = 30_000;

type SourceFilter = "" | "backend" | "frontend";

function SourcePill({ source }: { source: string }) {
  const frontend = source === "frontend";
  return (
    <span
      className={`inline-flex items-center rounded-sm border px-2 py-0.5 font-terminal text-[0.62rem] uppercase tracking-[0.14em] ${
        frontend
          ? "border-gold-700/40 text-gold-500"
          : "border-bone-muted/30 text-bone-muted"
      }`}
    >
      {frontend ? "Frontend" : "Backend"}
    </span>
  );
}

export function ErrorGroupsPanel() {
  const [page, setPage] = useState(1);
  const [source, setSource] = useState<SourceFilter>("");

  const query = useQuery({
    ...auditErrorGroupsListOptions({
      query: { page, ...(source ? { source } : {}) },
    }),
    placeholderData: keepPreviousData,
    refetchInterval: REFETCH_MS,
  });

  const data = query.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = query.isPlaceholderData;

  const columns: Array<ColumnDef<ErrorGroup>> = [
    {
      accessorKey: "source",
      header: "Source",
      cell: ({ row }) => <SourcePill source={row.original.source} />,
    },
    {
      accessorKey: "exception_class",
      header: "Type",
      cell: ({ row }) => {
        const { exception_class, status_code } = row.original;
        const label =
          exception_class || (status_code ? `HTTP ${status_code}` : "Error");
        return <span className="font-terminal text-xs text-bone">{label}</span>;
      },
    },
    {
      accessorKey: "message",
      header: "Latest message",
      cell: ({ row }) => (
        <div className="max-w-xl">
          <div className="truncate text-bone" title={row.original.message}>
            {row.original.message || "—"}
          </div>
          {row.original.path ? (
            <div className="truncate font-terminal text-[0.7rem] text-bone-muted">
              {row.original.path}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      accessorKey: "count",
      header: () => <div className="text-right">Count</div>,
      cell: ({ row }) => (
        <div className="text-right nums-terminal font-medium text-gold-300">
          {row.original.count}
        </div>
      ),
    },
    {
      accessorKey: "last_seen",
      header: () => <div className="text-right">Last seen</div>,
      cell: ({ row }) => (
        <div className="text-right font-terminal text-[0.7rem] text-bone-muted">
          {formatDateTimeUtc(row.original.last_seen)}
        </div>
      ),
    },
  ];

  return (
    <section>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="font-terminal text-[0.7rem] uppercase tracking-[0.16em] text-gold-700">
            Error triage
          </p>
          <h2 className="mt-1 font-display text-xl font-semibold tracking-tight text-bone">
            Grouped errors
          </h2>
        </div>
        <label className="flex items-center gap-2 font-terminal text-xs uppercase tracking-[0.12em] text-bone-muted">
          <span>Source</span>
          <select
            aria-label="Filter errors by source"
            value={source}
            onChange={(event) => {
              setSource(event.target.value as SourceFilter);
              setPage(1);
            }}
            className="h-8 rounded-sm border border-gold-900/25 bg-vault-900 px-2.5 font-terminal text-xs uppercase tracking-[0.12em] text-gold-300 outline-none focus-visible:border-gold-700/50 focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <option value="">All</option>
            <option value="backend">Backend</option>
            <option value="frontend">Frontend</option>
          </select>
        </label>
      </div>

      <div className="mt-4">
        {query.isPending ? (
          <TableSkeleton columnCount={columns.length} label="Loading errors" />
        ) : query.isError ? (
          <QueryErrorState
            title="Couldn't load errors."
            onRetry={() => query.refetch()}
            backLabel={page > 1 ? `Back to page ${page - 1}` : undefined}
            onBack={
              page > 1 ? () => setPage((current) => Math.max(1, current - 1)) : undefined
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
              emptyMessage="No errors recorded. Clean run."
            />
            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="error group"
              isPaging={isPaging}
              hasPrev={Boolean(data?.previous)}
              hasNext={Boolean(data?.next)}
              onPageChange={setPage}
            />
          </div>
        )}
      </div>
    </section>
  );
}
