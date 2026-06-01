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
  type Deck,
  type DeckRequest,
  decksDecksCreate,
  decksDecksListOptions,
  decksDecksListQueryKey,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { seedCsrf } from "@/lib/csrf";

// DRF serves one fixed page size globally (PageNumberPagination, PAGE_SIZE=100).
const PAGE_SIZE = 100;

/** Pull a DRF field-error string (`{field: ["..."]}` / `{detail: "..."}`) out of a body. */
function fieldError(error: unknown, field: string): string | null {
  if (error && typeof error === "object" && field in error) {
    const value = (error as Record<string, unknown>)[field];
    if (Array.isArray(value) && typeof value[0] === "string") return value[0];
    if (typeof value === "string") return value;
  }
  return null;
}

// Bare SDK fn (not the *Mutation helper) so we read response.status + the 400 body — the
// import/alert write pattern (DECISIONS 2026-05-30).
async function createDeck(body: DeckRequest): Promise<Deck> {
  const { data, error, response } = await decksDecksCreate({ body });
  if (!data) {
    // A 403 can be a missing/stale CSRF cookie; re-seed so a retry carries a token without a
    // reload (harmless for an auth 403).
    if (response?.status === 403) seedCsrf();
    const detail = fieldError(error, "name") ?? fieldError(error, "detail");
    const fallback = response
      ? `Couldn't create the deck (HTTP ${response.status}).`
      : "Couldn't create the deck: could not reach the server.";
    throw new Error(detail ?? fallback);
  }
  return data;
}

function CreateDeckForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const mutation = useMutation({
    mutationFn: createDeck,
    onSuccess: () => {
      setName("");
      setDescription("");
      onCreated();
    },
  });

  // Client guard mirrors the server (name required); the server's 400 is still the real
  // boundary.
  const invalid = name.trim() === "";

  return (
    <form
      className="rounded-lg border border-border p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (invalid || mutation.isPending) return;
        mutation.mutate({ name: name.trim(), description: description.trim() });
      }}
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">Deck name</span>
          <input
            type="text"
            value={name}
            aria-label="Deck name"
            placeholder="e.g. Snake-Eye"
            disabled={mutation.isPending}
            onChange={(event) => setName(event.target.value)}
            className="h-8 w-56 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">Description (optional)</span>
          <input
            type="text"
            value={description}
            aria-label="Description"
            placeholder="e.g. Tier 1 build"
            disabled={mutation.isPending}
            onChange={(event) => setDescription(event.target.value)}
            className="h-8 w-72 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          />
        </label>
        <Button type="submit" size="sm" disabled={invalid || mutation.isPending}>
          {mutation.isPending ? "Creating…" : "Create deck"}
        </Button>
      </div>

      {mutation.isError ? (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {mutation.error?.message}
        </p>
      ) : null}
      {mutation.isSuccess ? (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          Deck created.
        </p>
      ) : null}
    </form>
  );
}

const columns: Array<ColumnDef<Deck>> = [
  {
    accessorKey: "name",
    header: "Deck",
    cell: ({ row }) => (
      <Link
        href={`/decks/${row.original.id}`}
        className="font-medium text-foreground underline-offset-4 hover:underline"
      >
        {row.original.name}
      </Link>
    ),
  },
  {
    accessorKey: "description",
    header: "Description",
    cell: ({ row }) =>
      row.original.description?.trim() ? (
        <span className="text-muted-foreground">{row.original.description}</span>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "member_count",
    // Counts distinct tagged HOLDINGS, not physical cards — a holding can be N copies
    // (shown per-row on the deck detail). Labeled "Holdings" to stay honest.
    header: () => <div className="text-right">Holdings</div>,
    cell: ({ row }) => (
      <div className="text-right tabular-nums">{row.original.member_count}</div>
    ),
  },
];

export default function DecksPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);

  const decksQuery = useQuery({
    ...decksDecksListOptions({ query: { page } }),
    placeholderData: keepPreviousData,
  });

  function handleDeckCreated() {
    queryClient.invalidateQueries({ queryKey: decksDecksListQueryKey() });
  }

  const data = decksQuery.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = decksQuery.isPlaceholderData;
  const hasPrev = Boolean(data?.previous);
  const hasNext = Boolean(data?.next);

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Decks</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Group cards you own into decks. Open a deck to add or remove holdings.
        </p>
      </div>

      <div className="mt-6">
        <CreateDeckForm onCreated={handleDeckCreated} />
      </div>

      <div className="mt-8">
        {decksQuery.isPending ? (
          <TableSkeleton columnCount={columns.length} label="Loading decks" />
        ) : decksQuery.isError ? (
          <QueryErrorState
            title="Couldn't load your decks."
            onRetry={() => decksQuery.refetch()}
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
              emptyMessage="No decks yet. Create one above to start tagging cards from your collection."
            />
            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="deck"
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
