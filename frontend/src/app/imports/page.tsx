"use client";

import { useState } from "react";
import Link from "next/link";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import {
  type ImportBatch,
  importsBatchesListOptions,
  importsBatchesListQueryKey,
} from "@/lib/api";
import { DataTable } from "@/components/ui/data-table";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { ImportUpload } from "@/components/imports/import-upload";
import { BatchStatusPill } from "@/components/imports/status";
import { formatDayShort } from "@/lib/format";

// DRF PageNumberPagination, PAGE_SIZE=100 — cosmetic "page X of Y" only; Prev/Next
// enablement is driven by the API's next/previous links (the slice-3 rule).
const PAGE_SIZE = 100;

const columns: Array<ColumnDef<ImportBatch>> = [
  {
    accessorKey: "original_filename",
    header: "File",
    cell: ({ row }) => (
      <Link
        href={`/imports/${row.original.id}`}
        className="font-medium text-foreground underline-offset-4 hover:underline"
      >
        {row.original.original_filename}
      </Link>
    ),
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <BatchStatusPill status={row.original.status ?? "review"} />,
  },
  {
    accessorKey: "rows_total",
    header: () => <div className="text-right">Rows</div>,
    cell: ({ row }) => (
      <div className="text-right tabular-nums">{row.original.rows_total}</div>
    ),
  },
  {
    accessorKey: "rows_needs_review",
    header: () => <div className="text-right">Needs review</div>,
    cell: ({ row }) => {
      const needsReview = row.original.rows_needs_review;
      return (
        <div
          className={
            needsReview > 0
              ? "text-right font-medium tabular-nums text-foreground"
              : "text-right tabular-nums text-muted-foreground"
          }
        >
          {needsReview}
        </div>
      );
    },
  },
  {
    accessorKey: "created_at",
    header: "Imported",
    // created_at is an ISO datetime; formatDayShort wants a bare YYYY-MM-DD (UTC-pinned).
    cell: ({ row }) => formatDayShort(row.original.created_at.slice(0, 10)),
  },
];

export default function ImportsPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);

  const query = useQuery({
    ...importsBatchesListOptions({ query: { page } }),
    placeholderData: keepPreviousData,
  });

  const data = query.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = query.isPlaceholderData;
  const hasPrev = Boolean(data?.previous);
  const hasNext = Boolean(data?.next);

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Imports</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Upload a Dragon Shield CSV export, then review and approve its matches.
        </p>
      </div>

      <div className="mt-6">
        <ImportUpload
          onUploaded={() => {
            // Newest-first list; jump to page 1 so the new batch is visible, and
            // refetch every batch-list page (partial-key match).
            setPage(1);
            queryClient.invalidateQueries({
              queryKey: importsBatchesListQueryKey(),
            });
          }}
        />
      </div>

      <div className="mt-8">
        <h2 className="text-lg font-medium">Import history</h2>
        <div className="mt-3">
          {query.isPending ? (
            <TableSkeleton columnCount={columns.length} label="Loading imports" />
          ) : query.isError ? (
            <QueryErrorState
              title="Couldn't load your imports."
              onRetry={() => query.refetch()}
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
                emptyMessage="No imports yet. Upload a Dragon Shield CSV to get started."
              />
              <PaginationControls
                page={page}
                totalPages={totalPages}
                count={count}
                noun="import"
                isPaging={isPaging}
                hasPrev={hasPrev}
                hasNext={hasNext}
                onPageChange={setPage}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
