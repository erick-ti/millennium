"use client";

import { useState } from "react";
import Link from "next/link";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import {
  type ImportRow,
  type ImportsRowsListData,
  importsBatchesRetrieveOptions,
  importsBatchesRetrieveQueryKey,
  importsRowsApproveCreate,
  importsRowsListOptions,
  importsRowsListQueryKey,
  importsRowsOverrideCreate,
  importsRowsRejectCreate,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { DetailSkeleton } from "@/components/cards/detail-skeleton";
import {
  BatchStatusPill,
  ConfidencePill,
  Pill,
  RowStatusPill,
} from "@/components/imports/status";
import { PrintingPicker } from "@/components/imports/printing-picker";
import { formatDayShort } from "@/lib/format";
import { seedCsrf } from "@/lib/csrf";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 100;

// A single reviewer-facing "Show" filter (status + needs_review collapsed): the
// task-oriented "needs review" set is exactly the still-PENDING rows.
type RowFilter = "all" | "needs_review" | "materialized" | "skipped" | "error";

function filterQuery(filter: RowFilter): Partial<NonNullable<ImportsRowsListData["query"]>> {
  switch (filter) {
    case "needs_review":
      return { needs_review: true };
    case "materialized":
      return { status: "materialized" };
    case "skipped":
      return { status: "skipped" };
    case "error":
      return { status: "error" };
    default:
      return {};
  }
}

/** Safely read a string field from a row's `normalized_data` (typed `unknown`). */
function normField(row: ImportRow, key: string): string | null {
  const data = row.normalized_data;
  if (data && typeof data === "object" && key in data) {
    const value = (data as Record<string, unknown>)[key];
    if (typeof value === "string") return value;
    if (typeof value === "number") return String(value);
  }
  return null;
}

type ApproveOutcome = "materialized" | "skipped" | "conflict";

// Use the SDK fns (not the *Mutation helpers) so we can read response.status —
// approve returns 409 (a full ImportRow body) when the holding was already
// imported with a different quantity/cost, which we surface DISTINCTLY rather
// than treating as a generic error or overwriting cost basis.
// `response` is optional in the SDK result (a network error before a response leaves it
// undefined); on a 4xx the parsed body lands in `error`. Prefer the backend's explanatory
// {detail} (e.g. "batch N is Completed, not in Review") over a bare HTTP-code message.
function detailMessage(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function failure(action: string, error: unknown, response: Response | undefined): Error {
  // A 403 can be a missing/stale CSRF cookie (the on-mount seed raced or failed); re-seed so the
  // next attempt carries a token without a reload. Harmless for an auth 403 — the backend detail
  // message below still tells the user to sign in. (Codex review 2026-05-30.)
  if (response?.status === 403) seedCsrf();
  return new Error(
    detailMessage(error) ??
      (response
        ? `${action} failed (HTTP ${response.status}).`
        : `${action} failed: could not reach the server.`),
  );
}

async function approveRow(rowId: number): Promise<ApproveOutcome> {
  const { data, error, response } = await importsRowsApproveCreate({ path: { id: rowId } });
  if (response?.status === 409) return "conflict";
  if (!data) throw failure("Approve", error, response);
  return data.status === "materialized" ? "materialized" : "skipped";
}

async function rejectRow(rowId: number): Promise<void> {
  const { data, error, response } = await importsRowsRejectCreate({ path: { id: rowId } });
  if (!data) throw failure("Reject", error, response);
}

async function overrideRow(args: { rowId: number; printing: number }): Promise<void> {
  const { data, error, response } = await importsRowsOverrideCreate({
    path: { id: args.rowId },
    body: { printing: args.printing },
  });
  if (!data) throw failure("Override", error, response);
}

type Feedback = { tone: "green" | "amber" | "red" | "neutral"; text: string };

const FEEDBACK_CLASSES: Record<Feedback["tone"], string> = {
  green: "border-gain/30 bg-gain/10 text-gain",
  amber: "border-flat/30 bg-flat/10 text-flat",
  red: "border-destructive/30 bg-destructive/10 text-destructive",
  neutral: "border-border bg-muted/40 text-muted-foreground",
};

export function ImportBatchDetail({ batchId }: { batchId: number }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<RowFilter>("all");
  const [overridingRowId, setOverridingRowId] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const batchQuery = useQuery(
    importsBatchesRetrieveOptions({ path: { id: batchId } }),
  );

  const rowsQuery = useQuery({
    ...importsRowsListOptions({
      query: { batch: batchId, page, ...filterQuery(filter) },
    }),
    placeholderData: keepPreviousData,
  });

  function invalidate() {
    // Refetch this batch's rows (all pages/filters) and its header counts.
    queryClient.invalidateQueries({ queryKey: importsRowsListQueryKey() });
    queryClient.invalidateQueries({
      queryKey: importsBatchesRetrieveQueryKey({ path: { id: batchId } }),
    });
  }

  const approveMutation = useMutation({
    mutationFn: approveRow,
    onSuccess: (outcome) => {
      if (outcome === "conflict") {
        setFeedback({
          tone: "amber",
          text: "Already imported with a different quantity or cost — left pending so the existing cost basis isn't overwritten. Resolve via re-import or the admin.",
        });
      } else if (outcome === "materialized") {
        setFeedback({ tone: "green", text: "Approved and added to your collection." });
      } else {
        setFeedback({
          tone: "neutral",
          text: "Approved — this holding was already imported unchanged, so nothing was added.",
        });
      }
      invalidate();
    },
    onError: (error) => setFeedback({ tone: "red", text: error.message }),
  });

  const rejectMutation = useMutation({
    mutationFn: rejectRow,
    onSuccess: () => {
      setFeedback({ tone: "neutral", text: "Row rejected and skipped." });
      invalidate();
    },
    onError: (error) => setFeedback({ tone: "red", text: error.message }),
  });

  const overrideMutation = useMutation({
    mutationFn: overrideRow,
    onSuccess: () => {
      setFeedback({
        tone: "green",
        text: "Match updated — review and approve the corrected match.",
      });
      setOverridingRowId(null);
      invalidate();
    },
    onError: (error) => setFeedback({ tone: "red", text: error.message }),
  });

  // One shared mutation object per action can't reliably track WHICH row is in flight
  // (mutation.variables reflects only the latest mutate() call), so a second action would
  // re-enable the first row's buttons mid-request. Disable all row actions while ANY is
  // pending — at single-user scale serial actions are the norm, and this removes the
  // double-submit / stale-busy ambiguity entirely.
  const anyActionPending =
    approveMutation.isPending || rejectMutation.isPending || overrideMutation.isPending;

  // Page/filter navigation closes any open override picker so it can't stay detached from — or
  // submit against — a row that's no longer in the visible result set (Codex review 2026-05-30).
  // Done in the handlers (not an effect — react-hooks/set-state-in-effect) since those are the
  // only ways page/filter change.
  function goToPage(next: number) {
    setPage(next);
    setOverridingRowId(null);
  }
  function changeFilter(next: RowFilter) {
    setFilter(next);
    setPage(1);
    setOverridingRowId(null);
  }

  if (batchQuery.isPending) {
    return <DetailSkeleton label="import" />;
  }

  if (batchQuery.isError || batchQuery.data == null) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-10">
        <BackLink />
        <div className="mt-4">
          <QueryErrorState
            title="Couldn't load this import."
            onRetry={() => batchQuery.refetch()}
          />
        </div>
      </div>
    );
  }

  const batch = batchQuery.data;
  const rows = rowsQuery.data?.results ?? [];
  const count = rowsQuery.data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = rowsQuery.isPlaceholderData;
  const hasPrev = Boolean(rowsQuery.data?.previous);
  const hasNext = Boolean(rowsQuery.data?.next);
  const overridingRow = rows.find((row) => row.id === overridingRowId);
  // Row actions are inert while a mutation is in flight OR while a page/filter transition is
  // showing stale (keepPreviousData) rows — acting on a row that's about to scroll out of view
  // would terminally approve/skip the wrong row (Codex review 2026-05-30, the slice-3
  // "dim ⇒ non-actionable" rule).
  const actionsLocked = anyActionPending || isPaging;

  const columns: Array<ColumnDef<ImportRow>> = [
    {
      accessorKey: "row_number",
      header: () => <div className="text-right">#</div>,
      cell: ({ row }) => (
        <div className="text-right tabular-nums text-muted-foreground">
          {row.original.row_number}
        </div>
      ),
    },
    {
      id: "card",
      header: "Card",
      cell: ({ row }) => {
        const data = row.original;
        const name = data.matched_printing?.card_name ?? normField(data, "card_name") ?? "—";
        return (
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">{name}</span>
            {data.matched_printing?.is_multi_variant ? (
              <Pill tone="amber">multi-variant</Pill>
            ) : null}
          </div>
        );
      },
    },
    {
      id: "set",
      header: "Set",
      cell: ({ row }) =>
        row.original.matched_printing?.set_code ?? normField(row.original, "set_code") ?? "—",
    },
    {
      id: "rarity",
      header: "Rarity",
      cell: ({ row }) =>
        row.original.matched_printing?.set_rarity ?? normField(row.original, "set_rarity") ?? "—",
    },
    {
      id: "match",
      header: "Match",
      cell: ({ row }) => <ConfidencePill confidence={row.original.match_confidence ?? "unmatched"} />,
    },
    {
      id: "status",
      header: "Status",
      cell: ({ row }) => (
        <div className="flex flex-col gap-0.5">
          <RowStatusPill status={row.original.status ?? "pending"} />
          {row.original.error_message ? (
            <span
              className="max-w-48 truncate text-xs text-destructive"
              title={row.original.error_message}
            >
              {row.original.error_message}
            </span>
          ) : null}
        </div>
      ),
    },
    {
      id: "actions",
      header: () => <div className="text-right">Actions</div>,
      cell: ({ row }) => {
        const data = row.original;
        if (data.status !== "pending") {
          return <div className="text-right text-muted-foreground">—</div>;
        }
        const canApprove = data.matched_printing != null;
        return (
          <div className="flex justify-end gap-1.5">
            <Button
              size="xs"
              variant="outline"
              disabled={actionsLocked || !canApprove}
              onClick={() => approveMutation.mutate(data.id)}
              title={canApprove ? undefined : "No matched printing — use Override to pick one first."}
            >
              Approve
            </Button>
            <Button
              size="xs"
              variant="ghost"
              disabled={actionsLocked}
              onClick={() =>
                setOverridingRowId((prev) => (prev === data.id ? null : data.id))
              }
            >
              {overridingRowId === data.id ? "Close" : "Override"}
            </Button>
            <Button
              size="xs"
              variant="destructive"
              disabled={actionsLocked}
              onClick={() => rejectMutation.mutate(data.id)}
            >
              Reject
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <BackLink />
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">
        {batch.original_filename}
      </h1>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <BatchStatusPill status={batch.status ?? "review"} />
        <span>Imported {formatDayShort(batch.created_at.slice(0, 10))}</span>
        <span className="tabular-nums">
          {batch.rows_total} rows · {batch.rows_materialized} materialized ·{" "}
          {batch.rows_skipped} skipped · {batch.rows_needs_review} need review ·{" "}
          {batch.rows_error} errors
        </span>
      </div>
      {batch.status === "failed" && batch.error ? (
        <p className="mt-2 text-sm text-destructive">{batch.error}</p>
      ) : null}

      {feedback ? (
        <div
          role="status"
          className={cn(
            "mt-4 flex items-start justify-between gap-3 rounded-lg border p-3 text-sm",
            FEEDBACK_CLASSES[feedback.tone],
          )}
        >
          <span>{feedback.text}</span>
          <button
            type="button"
            onClick={() => setFeedback(null)}
            className="shrink-0 text-xs underline underline-offset-4"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      {overridingRowId != null ? (
        <div className="mt-4">
          <p className="mb-2 text-sm text-muted-foreground">
            Override match for row {overridingRow?.row_number}
            {overridingRow ? ` · ${normField(overridingRow, "card_name") ?? "—"}` : ""}
          </p>
          <PrintingPicker
            // Key by the target row so switching Override from row A to row B (which never
            // passes through null) REMOUNTS the picker, clearing its card/printing selection.
            // Without this, a printing picked in row A's context could apply to row B.
            key={overridingRowId}
            isSubmitting={overrideMutation.isPending}
            onCancel={() => setOverridingRowId(null)}
            onSelect={(printingId) =>
              overrideMutation.mutate({ rowId: overridingRowId, printing: printingId })
            }
          />
        </div>
      ) : null}

      <div className="mt-6 flex items-center justify-between gap-3">
        <h2 className="text-lg font-medium">Rows</h2>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>Show</span>
          <select
            value={filter}
            aria-label="Filter rows"
            onChange={(event) => changeFilter(event.target.value as RowFilter)}
            className="h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <option value="all">All rows</option>
            <option value="needs_review">Needs review</option>
            <option value="materialized">Materialized</option>
            <option value="skipped">Skipped</option>
            <option value="error">Errors</option>
          </select>
        </label>
      </div>

      <div className="mt-3">
        {rowsQuery.isPending ? (
          <TableSkeleton columnCount={columns.length} label="Loading rows" />
        ) : rowsQuery.isError ? (
          <QueryErrorState
            title="Couldn't load this import's rows."
            onRetry={() => rowsQuery.refetch()}
            backLabel={page > 1 ? `Back to page ${page - 1}` : undefined}
            onBack={page > 1 ? () => goToPage(Math.max(1, page - 1)) : undefined}
          />
        ) : (
          <div
            aria-busy={isPaging}
            className={isPaging ? "opacity-60 transition-opacity" : undefined}
          >
            <DataTable
              columns={columns}
              data={rows}
              emptyMessage={
                filter === "all"
                  ? "This import has no rows."
                  : "No rows match this filter."
              }
            />
            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="row"
              isPaging={isPaging}
              hasPrev={hasPrev}
              hasNext={hasNext}
              onPageChange={goToPage}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/imports"
      className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
    >
      ← Imports
    </Link>
  );
}
