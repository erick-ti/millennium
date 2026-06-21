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
  type AlertEvent,
  type AlertRule,
  type AlertRuleRequest,
  alertsEventsListOptions,
  alertsEventsListQueryKey,
  alertsRulesCreate,
  alertsRulesListOptions,
  alertsRulesListQueryKey,
} from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { ReadOnlyNotice } from "@/components/auth/read-only-notice";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import {
  formatDayShort,
  formatPercent,
  formatSignedUsd,
  parseDecimal,
} from "@/lib/format";
import { seedCsrf } from "@/lib/csrf";

// DRF serves one fixed page size globally (PageNumberPagination, PAGE_SIZE=100).
const PAGE_SIZE = 100;

type WindowDays = 7 | 30 | 90;
type Direction = "up" | "down" | "any";
const WINDOW_CHOICES: ReadonlyArray<WindowDays> = [7, 30, 90];
const DIRECTION_CHOICES: ReadonlyArray<{ value: Direction; label: string }> = [
  { value: "any", label: "Any direction" },
  { value: "up", label: "Up only" },
  { value: "down", label: "Down only" },
];
const EDITION_LABELS: Record<string, string> = {
  first: "1st Edition",
  unlimited: "Unlimited",
  limited: "Limited",
};
const DIRECTION_LABELS: Record<string, string> = {
  up: "up",
  down: "down",
  any: "any direction",
};

// Gain → emerald, loss → red, flat → muted (the movers / portfolio-metrics convention).
function deltaColorClass(value: number | null): string {
  if (value == null || value === 0) {
    return "text-flat";
  }
  return value > 0 ? "text-gain" : "text-loss";
}

// The ▲/▼ glyph that always accompanies a colored delta (CVD-safe — never color
// alone, the landing/movers rule). Flat/null carries no directional glyph.
function deltaGlyph(value: number | null): string {
  if (value == null || value === 0) {
    return "";
  }
  return value > 0 ? "▲" : "▼";
}

/** Pull a DRF field-error string (`{field: ["..."]}` or `{detail: "..."}`) out of a body. */
function fieldError(error: unknown, field: string): string | null {
  if (error && typeof error === "object" && field in error) {
    const value = (error as Record<string, unknown>)[field];
    if (Array.isArray(value) && typeof value[0] === "string") return value[0];
    if (typeof value === "string") return value;
  }
  return null;
}

// Use the bare SDK fn (not the *Mutation helper) so we read response.status and the 400
// body directly — the import-write pattern (DECISIONS 2026-05-30).
async function createRule(body: AlertRuleRequest): Promise<AlertRule> {
  const { data, error, response } = await alertsRulesCreate({ body });
  if (!data) {
    // A 403 can be a missing/stale CSRF cookie; re-seed so a retry carries a token without a
    // reload (harmless for an auth 403) — the same recovery the import writes use.
    if (response?.status === 403) seedCsrf();
    const detail =
      fieldError(error, "name") ??
      fieldError(error, "threshold_pct") ??
      fieldError(error, "window_days") ??
      fieldError(error, "direction") ??
      fieldError(error, "detail");
    const fallback = response
      ? `Couldn't create the rule (HTTP ${response.status}).`
      : "Couldn't create the rule: could not reach the server.";
    throw new Error(detail ?? fallback);
  }
  return data;
}

function CreateRuleForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [threshold, setThreshold] = useState("10");
  const [windowDays, setWindowDays] = useState<WindowDays>(30);
  const [direction, setDirection] = useState<Direction>("any");

  const mutation = useMutation({
    mutationFn: createRule,
    onSuccess: () => {
      setName("");
      setThreshold("10");
      setWindowDays(30);
      setDirection("any");
      onCreated();
    },
  });

  // Client-side guard mirrors the server (name required, threshold > 0) so the submit is
  // disabled rather than round-tripping an obviously-invalid rule; the server is still the
  // real boundary (its CHECKs + serializer 400).
  const thresholdNum = Number(threshold);
  const invalid = name.trim() === "" || !Number.isFinite(thresholdNum) || thresholdNum <= 0;

  return (
    <form
      className="vitrine rounded-lg p-5 sm:p-6"
      onSubmit={(event) => {
        event.preventDefault();
        if (invalid || mutation.isPending) return;
        mutation.mutate({
          name: name.trim(),
          threshold_pct: threshold,
          window_days: windowDays,
          direction,
        });
      }}
    >
      <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
        New rule
      </h2>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-bone-muted">Name</span>
          <input
            type="text"
            value={name}
            aria-label="Rule name"
            placeholder="e.g. Big weekly movers"
            disabled={mutation.isPending}
            onChange={(event) => setName(event.target.value)}
            className="h-8 w-56 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-bone-muted">Threshold %</span>
          <input
            type="number"
            min="0.01"
            step="0.01"
            value={threshold}
            // Accessible name matches the visible "Threshold %" (WCAG 2.5.3 label-in-name —
            // a voice-control user says what they see); screen readers still read "%" as
            // "percent", so this reads naturally too.
            aria-label="Threshold %"
            disabled={mutation.isPending}
            onChange={(event) => setThreshold(event.target.value)}
            className="nums-terminal h-8 w-28 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-bone-muted">Window</span>
          <select
            value={windowDays}
            aria-label="Window"
            disabled={mutation.isPending}
            onChange={(event) => setWindowDays(Number(event.target.value) as WindowDays)}
            className="h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          >
            {WINDOW_CHOICES.map((days) => (
              <option key={days} value={days}>
                {days} days
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-bone-muted">Direction</span>
          <select
            value={direction}
            aria-label="Direction"
            disabled={mutation.isPending}
            onChange={(event) => setDirection(event.target.value as Direction)}
            className="h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          >
            {DIRECTION_CHOICES.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
        <Button type="submit" size="sm" disabled={invalid || mutation.isPending}>
          {mutation.isPending ? "Creating…" : "Create rule"}
        </Button>
      </div>

      {mutation.isError ? (
        <p role="alert" className="mt-3 text-sm text-loss">
          {mutation.error?.message}
        </p>
      ) : null}
      {mutation.isSuccess ? (
        <p role="status" className="mt-3 text-sm text-bone-muted">
          Rule created — it will be evaluated on the next daily run.
        </p>
      ) : null}
    </form>
  );
}

function PctMoveCell({ value }: { value: string }) {
  // pct_change is stored as a HUMAN percent ("20.00" = +20%); formatPercent expects a
  // RATIO (it ×100s), so divide by 100. Non-null on every event (the model field is NOT
  // NULL — an event only fires on a real, above-floor percent move).
  const pct = parseDecimal(value);
  const glyph = deltaGlyph(pct);
  return (
    <div className={`nums-terminal text-right ${deltaColorClass(pct)}`}>
      {pct == null ? (
        "—"
      ) : (
        <>
          {glyph ? <span aria-hidden>{glyph} </span> : null}
          {formatPercent(pct / 100)}
        </>
      )}
    </div>
  );
}

function DollarMoveCell({ value }: { value: string }) {
  const delta = parseDecimal(value);
  const glyph = deltaGlyph(delta);
  return (
    <div className={`nums-terminal text-right ${deltaColorClass(delta)}`}>
      {delta == null ? (
        "—"
      ) : (
        <>
          {glyph ? <span aria-hidden>{glyph} </span> : null}
          {formatSignedUsd(delta)}
        </>
      )}
    </div>
  );
}

export default function AlertsPage() {
  const queryClient = useQueryClient();
  const { canWrite, isLoading: authLoading } = useAuth();
  const [page, setPage] = useState(1);
  const [ruleFilter, setRuleFilter] = useState<number | "all">("all");

  // Rules for the feed filter dropdown. Loads only the first page (PAGE_SIZE=100) — a
  // deliberate scope cap: a single user won't define >100 alert rules. Page-walk the
  // dropdown (or add a compact rule-options endpoint) if that ever changes.
  const rulesQuery = useQuery(alertsRulesListOptions({ query: {} }));

  const eventsQuery = useQuery({
    ...alertsEventsListOptions({
      query: { page, rule: ruleFilter === "all" ? undefined : ruleFilter },
    }),
    placeholderData: keepPreviousData,
  });

  function handleRuleCreated() {
    // A new rule changes the filter dropdown immediately; the feed updates after the next
    // daily evaluation, but invalidate it too so a manual `run_alerts` shows up on refetch.
    queryClient.invalidateQueries({ queryKey: alertsRulesListQueryKey() });
    queryClient.invalidateQueries({ queryKey: alertsEventsListQueryKey() });
  }

  function changeRuleFilter(next: number | "all") {
    setRuleFilter(next);
    setPage(1);
  }

  const data = eventsQuery.data;
  const count = data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const isPaging = eventsQuery.isPlaceholderData;
  const hasPrev = Boolean(data?.previous);
  const hasNext = Boolean(data?.next);
  const rules = rulesQuery.data?.results ?? [];

  const columns: Array<ColumnDef<AlertEvent>> = [
    {
      id: "rule",
      header: "Rule",
      cell: ({ row }) => {
        const event = row.original;
        return (
          <div>
            <div className="font-medium text-bone">{event.rule_name}</div>
            <div className="nums-terminal font-terminal text-xs text-bone-muted">
              ≥{event.rule_threshold_pct}% · {event.rule_window_days}d ·{" "}
              {DIRECTION_LABELS[event.rule_direction] ?? event.rule_direction}
            </div>
          </div>
        );
      },
    },
    {
      accessorKey: "card_name",
      header: "Card",
      cell: ({ row }) => (
        <Link
          href={`/cards/${row.original.card_id}`}
          className="font-medium text-gold-300 underline-offset-4 transition-colors hover:text-gold-500 hover:underline"
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
          <div className="text-bone">{row.original.set_code}</div>
          <div className="text-xs text-bone-muted">
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
      accessorKey: "pct_change",
      header: () => <div className="text-right">% Move</div>,
      cell: ({ row }) => <PctMoveCell value={row.original.pct_change} />,
    },
    {
      accessorKey: "dollar_change",
      header: () => <div className="text-right">$ Move</div>,
      cell: ({ row }) => <DollarMoveCell value={row.original.dollar_change} />,
    },
    {
      accessorKey: "triggered_on",
      header: () => <div className="text-right">Triggered</div>,
      cell: ({ row }) => (
        <div className="nums-terminal text-right text-bone-muted">
          {formatDayShort(row.original.triggered_on)}
        </div>
      ),
    },
  ];

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <PageHeader
        kicker="SIGNALS"
        title="Price alerts"
        subtitle="Watch any card for a percent move over a window; matching rules fire once a day."
      />

      <div>
        {canWrite ? (
          <CreateRuleForm onCreated={handleRuleCreated} />
        ) : authLoading ? null : (
          // Only show the demo notice once auth has settled (cold-load probe window).
          <ReadOnlyNotice>The demo can browse alerts but not create rules.</ReadOnlyNotice>
        )}
      </div>

      <div className="mt-8 flex items-center justify-between gap-3">
        <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
          Alert feed
        </h2>
        <label className="flex items-center gap-2 text-sm text-bone-muted">
          <span>Rule</span>
          <select
            value={ruleFilter === "all" ? "all" : String(ruleFilter)}
            aria-label="Filter by rule"
            onChange={(event) =>
              changeRuleFilter(
                event.target.value === "all" ? "all" : Number(event.target.value),
              )
            }
            className="h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <option value="all">All rules</option>
            {rules.map((rule) => (
              <option key={rule.id} value={rule.id}>
                {rule.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-3">
        {eventsQuery.isPending ? (
          <TableSkeleton columnCount={columns.length} label="Loading alerts" />
        ) : eventsQuery.isError ? (
          <QueryErrorState
            title="Couldn't load alerts."
            onRetry={() => eventsQuery.refetch()}
            // keepPreviousData drops the kept page on error, so a failure on page >1
            // would otherwise blank the table.
            backLabel={page > 1 ? `Back to page ${page - 1}` : undefined}
            onBack={
              page > 1 ? () => setPage((current) => Math.max(1, current - 1)) : undefined
            }
          />
        ) : count === 0 && ruleFilter === "all" ? (
          // Genuinely-empty (unfiltered) feed → the lit display-case empty state
          // explaining what a fired alert is. A rule-FILTERED empty result instead
          // keeps the table chrome + the in-table "No alerts match this filter."
          // message (the collection reference's count===0 && !hasFilter pattern) —
          // "No alerts have fired yet" would misread when other rules have fired.
          <EmptyState
            title="No alerts have fired yet"
            description="A fired alert is logged when a card you own crosses a rule's percent threshold over its window. Create a rule above — matches appear here after the daily evaluation runs."
          />
        ) : (
          <div
            aria-busy={isPaging}
            className={isPaging ? "opacity-60 transition-opacity" : undefined}
          >
            <DataTable
              columns={columns}
              data={data?.results ?? []}
              emptyMessage="No alerts match this filter."
            />
            <PaginationControls
              page={page}
              totalPages={totalPages}
              count={count}
              noun="alert"
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
