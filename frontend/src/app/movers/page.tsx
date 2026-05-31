"use client";

import { useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import { type MoverRow, valuationMoversListOptions } from "@/lib/api";
import { DataTable } from "@/components/ui/data-table";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import {
  formatDayShort,
  formatPercent,
  formatSignedUsd,
  formatUsd,
  parseDecimal,
} from "@/lib/format";

// DRF serves one fixed page size globally (PageNumberPagination, PAGE_SIZE=100).
// Cosmetic only here — Prev/Next enablement uses the authoritative next/previous
// links (slice 3 pattern).
const PAGE_SIZE = 100;

// Mirror the backend allowlists (apps/valuation/movers.py). Keeping these as
// literal unions means an out-of-set value can't be sent (and the generated query
// type would reject it anyway).
type WindowDays = 7 | 30 | 90;
type Ordering = "-pct_change" | "pct_change" | "-abs_change" | "abs_change";
const WINDOW_CHOICES: ReadonlyArray<WindowDays> = [7, 30, 90];

const EDITION_LABELS: Record<string, string> = {
  first: "1st Edition",
  unlimited: "Unlimited",
  limited: "Limited",
};

// Gain → emerald, loss → red, flat/unknown → muted (the portfolio-metrics
// convention). A null delta (no percent under the floor) is muted, never colored.
function deltaColorClass(value: number | null): string {
  if (value == null || value === 0) {
    return "text-muted-foreground";
  }
  return value > 0
    ? "text-emerald-600 dark:text-emerald-500"
    : "text-red-600 dark:text-red-500";
}

function PriceCell({ value, day }: { value: string; day: string }) {
  const price = parseDecimal(value);
  return (
    <div className="text-right tabular-nums">
      <div>{price == null ? "—" : formatUsd(price)}</div>
      <div className="text-xs text-muted-foreground">{formatDayShort(day)}</div>
    </div>
  );
}

function DeltaUsdCell({ value }: { value: string }) {
  const delta = parseDecimal(value);
  return (
    <div className={`text-right tabular-nums ${deltaColorClass(delta)}`}>
      {delta == null ? "—" : formatSignedUsd(delta)}
    </div>
  );
}

function DeltaPctCell({ value }: { value: number | null }) {
  return (
    <div className={`text-right tabular-nums ${deltaColorClass(value)}`}>
      {value == null ? (
        // Sub-floor base price → percent is undefined, not 0% (partial ≠ zero).
        <span title="Base price too low for a meaningful percent">—</span>
      ) : (
        formatPercent(value)
      )}
    </div>
  );
}

// The aria-sort value for a sortable column header given the active ordering —
// "descending"/"ascending" when this field is the active sort, else "none"
// (sortable but not currently sorted). Set on the column meta so DataTable can
// put it on the <th> for assistive tech.
function ariaSortFor(
  field: "pct_change" | "abs_change",
  ordering: Ordering,
): "ascending" | "descending" | "none" {
  if (ordering === `-${field}`) {
    return "descending";
  }
  if (ordering === field) {
    return "ascending";
  }
  return "none";
}

function SortableHeader({
  label,
  field,
  ordering,
  onToggle,
}: {
  label: string;
  field: "pct_change" | "abs_change";
  ordering: Ordering;
  onToggle: (field: "pct_change" | "abs_change") => void;
}) {
  const descending = ordering === `-${field}`;
  const active = descending || ordering === field;
  const indicator = active ? (descending ? "▼" : "▲") : "↕";
  return (
    <div className="text-right">
      <button
        type="button"
        onClick={() => onToggle(field)}
        aria-label={
          active
            ? `Sort by ${label}, currently ${descending ? "descending" : "ascending"}`
            : `Sort by ${label}`
        }
        className="inline-flex items-center gap-1 rounded font-medium text-foreground outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span>{label}</span>
        <span aria-hidden className={active ? undefined : "text-muted-foreground"}>
          {indicator}
        </span>
      </button>
    </div>
  );
}

export default function MoversPage() {
  const [page, setPage] = useState(1);
  const [windowDays, setWindowDays] = useState<WindowDays>(30);
  const [ordering, setOrdering] = useState<Ordering>("-pct_change");

  // Toggle direction if already sorting by this field, else activate it
  // descending; sorting is server-side, so reset to page 1.
  function toggleSort(field: "pct_change" | "abs_change") {
    setOrdering((current) =>
      current === `-${field}` ? (field as Ordering) : (`-${field}` as Ordering),
    );
    setPage(1);
  }

  const moversQuery = useQuery({
    ...valuationMoversListOptions({
      query: { page, window: windowDays, ordering },
    }),
    placeholderData: keepPreviousData,
  });

  const data = moversQuery.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = moversQuery.isPlaceholderData;
  const hasPrev = Boolean(data?.previous);
  const hasNext = Boolean(data?.next);

  const columns: Array<ColumnDef<MoverRow>> = [
    {
      accessorKey: "card_name",
      header: "Card",
      cell: ({ row }) => (
        <Link
          href={`/cards/${row.original.card_id}`}
          className="font-medium text-foreground underline-offset-4 hover:underline"
        >
          {row.original.card_name}
        </Link>
      ),
    },
    {
      accessorKey: "set_code",
      header: "Printing",
      cell: ({ row }) => (
        <div>
          <div>{row.original.set_code}</div>
          <div className="text-xs text-muted-foreground">
            {row.original.set_rarity}
            {row.original.variant_label ? ` · ${row.original.variant_label}` : ""}
          </div>
        </div>
      ),
    },
    {
      accessorKey: "edition",
      header: "Edition",
      cell: ({ row }) =>
        EDITION_LABELS[row.original.edition] ?? row.original.edition,
    },
    {
      accessorKey: "start_price",
      header: () => <div className="text-right">Start</div>,
      cell: ({ row }) => (
        <PriceCell value={row.original.start_price} day={row.original.start_date} />
      ),
    },
    {
      accessorKey: "end_price",
      header: () => <div className="text-right">Latest</div>,
      cell: ({ row }) => (
        <PriceCell value={row.original.end_price} day={row.original.end_date} />
      ),
    },
    {
      accessorKey: "abs_change",
      meta: { ariaSort: ariaSortFor("abs_change", ordering) },
      header: () => (
        <SortableHeader
          label="$ Change"
          field="abs_change"
          ordering={ordering}
          onToggle={toggleSort}
        />
      ),
      cell: ({ row }) => <DeltaUsdCell value={row.original.abs_change} />,
    },
    {
      accessorKey: "pct_change",
      meta: { ariaSort: ariaSortFor("pct_change", ordering) },
      header: () => (
        <SortableHeader
          label="% Change"
          field="pct_change"
          ordering={ordering}
          onToggle={toggleSort}
        />
      ),
      cell: ({ row }) => <DeltaPctCell value={row.original.pct_change} />,
    },
  ];

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Movers</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Biggest price movers among the cards you own, over your selected window.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>Window</span>
          <select
            aria-label="Lookback window"
            value={windowDays}
            onChange={(event) => {
              setWindowDays(Number(event.target.value) as WindowDays);
              setPage(1);
            }}
            className="h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {WINDOW_CHOICES.map((days) => (
              <option key={days} value={days}>
                {days} days
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-6">
        {moversQuery.isPending ? (
          <TableSkeleton columnCount={columns.length} label="Loading movers" />
        ) : moversQuery.isError ? (
          <QueryErrorState
            title="Couldn't load movers."
            onRetry={() => moversQuery.refetch()}
            // keepPreviousData drops the kept page on error, so a failure on
            // page >1 would otherwise blank the table.
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
              emptyMessage="No movers. Owned cards need a usable price at both the start and end of the window."
            />
            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="mover"
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
