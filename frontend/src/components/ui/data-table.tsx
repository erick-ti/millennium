"use client";

import {
  type ColumnDef,
  type RowData,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

declare module "@tanstack/react-table" {
  // Optional per-column metadata. `ariaSort` marks the active server-side sort
  // direction on a sortable header cell (rendered as `aria-sort` on the `<th>`)
  // so assistive tech announces which column is sorted and which way — the
  // movers view (Phase 5 slice 3) is the first server-sortable table. Left unset
  // on non-sortable columns and on the other (unsorted) read views, so no
  // `aria-sort` attribute is emitted there.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    ariaSort?: "ascending" | "descending" | "none";
  }
}

interface DataTableProps<TData, TValue> {
  columns: Array<ColumnDef<TData, TValue>>;
  data: Array<TData>;
  /** Shown in a single full-width row when `data` is empty. */
  emptyMessage?: string;
}

/**
 * A column-driven table over TanStack Table's core row model. Pagination,
 * filtering, and sorting are handled server-side by the caller (the read-API
 * is page-number paginated and has a fixed order), so this stays a thin
 * presentational shell — `getCoreRowModel` only. Reused across the collection
 * (slice 3), cards (slice 4), and portfolio (slice 5) views.
 */
export function DataTable<TData, TValue>({
  columns,
  data,
  emptyMessage = "No results.",
}: DataTableProps<TData, TValue>) {
  // TanStack Table's `useReactTable` returns functions React Compiler can't
  // safely memoize, so it bails out of optimizing this component. That's the
  // expected, safe behavior (the table re-renders on `data`/`columns` change
  // anyway), so silence the advisory rule rather than carry a perpetual warning.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <TableHead
                  key={header.id}
                  aria-sort={header.column.columnDef.meta?.ariaSort}
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(
                        header.column.columnDef.header,
                        header.getContext()
                      )}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(
                      cell.column.columnDef.cell,
                      cell.getContext()
                    )}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell
                colSpan={table.getVisibleLeafColumns().length}
                className="h-28 text-center font-display text-base text-bone-muted"
              >
                {emptyMessage}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}
