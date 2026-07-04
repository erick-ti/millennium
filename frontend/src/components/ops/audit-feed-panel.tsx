"use client";

import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import { type AuditEvent, auditEventsListOptions } from "@/lib/api";
import { DataTable } from "@/components/ui/data-table";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { formatDateTimeUtc } from "@/lib/format";

const PAGE_SIZE = 100;
const REFETCH_MS = 20_000;

type ActorType = "" | "anonymous" | "demo" | "user";
type Method = "" | "POST" | "PUT" | "PATCH" | "DELETE";

const ACTOR_PILL: Record<string, string> = {
  user: "border-gold-700/40 text-gold-500",
  demo: "border-gold-900/40 text-gold-700",
  anonymous: "border-bone-muted/25 text-bone-muted",
};

function ActorCell({ event }: { event: AuditEvent }) {
  // actor_type carries a model default, so the generated type marks it optional.
  const actorType = event.actor_type ?? "anonymous";
  return (
    <div>
      <span
        className={`inline-flex items-center rounded-sm border px-2 py-0.5 font-terminal text-[0.6rem] uppercase tracking-[0.14em] ${
          ACTOR_PILL[actorType] ?? ACTOR_PILL.anonymous
        }`}
      >
        {actorType}
      </span>
      {event.actor_username ? (
        <div className="mt-0.5 font-terminal text-[0.7rem] text-bone-muted">
          {event.actor_username}
        </div>
      ) : null}
    </div>
  );
}

// 2xx → flat, 3xx → flat, 4xx → amber/gold-700, 5xx → loss-red.
function statusColor(status: number): string {
  if (status >= 500) return "text-loss";
  if (status >= 400) return "text-gold-700";
  return "text-flat";
}

export function AuditFeedPanel() {
  const [page, setPage] = useState(1);
  const [actorType, setActorType] = useState<ActorType>("");
  const [method, setMethod] = useState<Method>("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Debounce in the handler (a timeout), NOT a setState-in-effect (the imports-review
  // convention). The cleanup effect only clears the timer — no state set in render.
  function onSearchChange(value: string) {
    setSearchInput(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setSearch(value.trim());
      setPage(1);
    }, 300);
  }
  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  const query = useQuery({
    ...auditEventsListOptions({
      query: {
        page,
        ...(actorType ? { actor_type: actorType } : {}),
        ...(method ? { method } : {}),
        ...(search ? { search } : {}),
      },
    }),
    placeholderData: keepPreviousData,
    refetchInterval: REFETCH_MS,
  });

  const data = query.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = query.isPlaceholderData;

  const columns: Array<ColumnDef<AuditEvent>> = [
    {
      accessorKey: "created_at",
      header: "Time",
      cell: ({ row }) => (
        <span className="font-terminal text-[0.7rem] text-bone-muted">
          {formatDateTimeUtc(row.original.created_at)}
        </span>
      ),
    },
    {
      accessorKey: "actor_type",
      header: "Actor",
      cell: ({ row }) => <ActorCell event={row.original} />,
    },
    {
      accessorKey: "path",
      header: "Action",
      cell: ({ row }) => (
        <div className="max-w-md">
          <div className="truncate text-bone" title={row.original.path}>
            <span className="font-terminal text-[0.7rem] text-gold-700">
              {row.original.method}
            </span>{" "}
            {row.original.path}
          </div>
          {row.original.view_name ? (
            <div className="truncate font-terminal text-[0.7rem] text-bone-muted">
              {row.original.view_name}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      accessorKey: "status_code",
      header: () => <div className="text-right">Status</div>,
      cell: ({ row }) => (
        <div
          className={`text-right nums-terminal ${statusColor(row.original.status_code)}`}
        >
          {row.original.status_code}
        </div>
      ),
    },
    {
      accessorKey: "duration_ms",
      header: () => <div className="text-right">Took</div>,
      cell: ({ row }) => (
        <div className="text-right font-terminal text-[0.7rem] text-bone-muted">
          {row.original.duration_ms == null ? "—" : `${row.original.duration_ms}ms`}
        </div>
      ),
    },
  ];

  return (
    <section>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="font-terminal text-[0.7rem] uppercase tracking-[0.16em] text-gold-700">
            Audit trail
          </p>
          <h2 className="mt-1 font-display text-xl font-semibold tracking-tight text-bone">
            Write activity
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="search"
            aria-label="Search audit events"
            placeholder="Search path / user…"
            value={searchInput}
            onChange={(event) => onSearchChange(event.target.value)}
            className="h-8 w-44 rounded-sm border border-gold-900/25 bg-vault-900 px-2.5 font-terminal text-xs text-bone outline-none placeholder:text-bone-muted/60 focus-visible:border-gold-700/50 focus-visible:ring-3 focus-visible:ring-ring/50"
          />
          <select
            aria-label="Filter by actor"
            value={actorType}
            onChange={(event) => {
              setActorType(event.target.value as ActorType);
              setPage(1);
            }}
            className="h-8 rounded-sm border border-gold-900/25 bg-vault-900 px-2.5 font-terminal text-xs uppercase tracking-[0.12em] text-gold-300 outline-none focus-visible:border-gold-700/50 focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <option value="">All actors</option>
            <option value="user">User</option>
            <option value="demo">Demo</option>
            <option value="anonymous">Anonymous</option>
          </select>
          <select
            aria-label="Filter by method"
            value={method}
            onChange={(event) => {
              setMethod(event.target.value as Method);
              setPage(1);
            }}
            className="h-8 rounded-sm border border-gold-900/25 bg-vault-900 px-2.5 font-terminal text-xs uppercase tracking-[0.12em] text-gold-300 outline-none focus-visible:border-gold-700/50 focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <option value="">All methods</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
            <option value="DELETE">DELETE</option>
          </select>
        </div>
      </div>

      <div className="mt-4">
        {query.isPending ? (
          <TableSkeleton columnCount={columns.length} label="Loading audit events" />
        ) : query.isError ? (
          <QueryErrorState
            title="Couldn't load audit events."
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
              emptyMessage="No matching audit events."
            />
            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="event"
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
