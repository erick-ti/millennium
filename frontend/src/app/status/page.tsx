"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  type RecentRun,
  statusChecksRetrieveOptions,
  statusInfraRetrieveOptions,
  statusOverviewRetrieveOptions,
} from "@/lib/api";
import { InfraTile } from "@/components/status/infra-tile";
import { PipelineFlow } from "@/components/status/pipeline-flow";
import { MetricRow, StatusTile } from "@/components/status/status-tile";
import { QueryErrorState } from "@/components/ui/query-error-state";
import { formatDateTimeUtc, formatDayShort, formatUsd, parseDecimal } from "@/lib/format";

const LOCALE = "en-US";

// The internal tier is cheap local DB reads (the heart of the page), so it polls fast
// for a near-live feel; the external Healthchecks tier is server-cached (~60s), so it
// polls at that cadence. The *Options spread merges these extra useQuery options.
const REFETCH_MS = 20_000;
const CHECKS_REFETCH_MS = 60_000;
// The infra tier is an internal DB read (uncached) like overview, but the host sampler
// only writes every ~2 min, so polling every 30s catches a new sample promptly without
// hammering for unchanged data.
const INFRA_REFETCH_MS = 30_000;

const RUN_KIND_LABELS: Record<string, string> = {
  ygoprodeck_metadata: "Metadata",
  tcgcsv_pricing: "Pricing",
};

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86_400);
  const h = Math.floor((seconds % 86_400) / 3_600);
  const m = Math.floor((seconds % 3_600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${seconds}s`;
}

function RecentRuns({ runs }: { runs: RecentRun[] }) {
  if (runs.length === 0) return null;
  return (
    <section>
      <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
        Recent sync runs
      </h2>
      <ol className="mt-3 divide-y divide-border overflow-hidden rounded-lg border border-border">
        {runs.map((run, i) => {
          const ok = run.status === "success";
          const pricing = run.kind === "tcgcsv_pricing";
          const count = pricing ? run.price_row_count : run.card_count;
          return (
            <li
              key={`${run.created_at}-${i}`}
              className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2 text-xs"
            >
              <span className="flex items-center gap-2">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-gain" : "bg-loss"}`}
                  aria-hidden
                />
                <span className="text-foreground">
                  {RUN_KIND_LABELS[run.kind] ?? run.kind}
                </span>
                <span className={ok ? "text-gain" : "text-loss"}>
                  {ok ? "success" : "failed"}
                </span>
              </span>
              <span className="flex items-center gap-3 font-terminal tabular-nums text-muted-foreground">
                {count != null ? (
                  <span>
                    {count.toLocaleString(LOCALE)} {pricing ? "prices" : "cards"}
                  </span>
                ) : null}
                <span>{formatDateTimeUtc(run.created_at)}</span>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export default function StatusPage() {
  const query = useQuery({
    ...statusOverviewRetrieveOptions(),
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  });

  // The Healthchecks tier loads/refreshes INDEPENDENTLY — a slow/down external provider
  // degrades the backup/CD flow nodes alone (grey), never the live internal flow.
  const checksQuery = useQuery({
    ...statusChecksRetrieveOptions(),
    refetchInterval: CHECKS_REFETCH_MS,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  });

  // The host-box tier loads INDEPENDENTLY too — it degrades to "awaiting host metrics"
  // on its own (no sampler yet / a stale sample) without touching the live flow.
  const infraQuery = useQuery({
    ...statusInfraRetrieveOptions(),
    refetchInterval: INFRA_REFETCH_MS,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  });

  // Keep the last-good dashboard on a transient poll failure (a single failed
  // refetch must not blank a live dashboard); only the FIRST load shows the error
  // panel. `query.data` survives a failed refetch, so `overview` stays populated.
  const overview = query.data;

  let liveText: string;
  let liveDot: string;
  if (query.isError) {
    liveText = "Disconnected — showing last good";
    liveDot = "bg-loss";
  } else if (query.isFetching) {
    liveText = "Refreshing…";
    liveDot = "bg-gain animate-pulse";
  } else {
    liveText = "Live · auto-refreshes";
    liveDot = "bg-gain";
  }

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Status</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            How a day flows through the system — the live pipeline, the catalog it
            maintains, and the box it runs on.
          </p>
        </div>
        <div className="flex items-center gap-2 font-terminal text-xs text-muted-foreground">
          <span className={`h-2 w-2 rounded-full ${liveDot}`} aria-hidden />
          {liveText}
        </div>
      </header>

      {/* SR-only announcer: speak ONLY the meaningful connection-state change, never the
          20s polling tick (a timer-driven aria-live region is screen-reader noise). The
          visible indicator above is intentionally not a live region. */}
      <p className="sr-only" aria-live="polite">
        {query.isError ? "Connection lost — showing the last known status." : ""}
      </p>

      <div className="mt-8">
        {query.isPending ? (
          <p className="text-sm text-muted-foreground" role="status">
            Loading status…
          </p>
        ) : overview ? (
          <div className="space-y-8">
            <PipelineFlow
              stages={overview.pipeline}
              checks={checksQuery.data}
              checksError={checksQuery.isError && checksQuery.data === undefined}
            />

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatusTile title="Catalog">
                <dl className="space-y-0.5">
                  <MetricRow label="Cards">
                    {overview.catalog.cards.toLocaleString(LOCALE)}
                  </MetricRow>
                  <MetricRow label="Printings">
                    {overview.catalog.printings.toLocaleString(LOCALE)}
                  </MetricRow>
                  <MetricRow label="Price snapshots">
                    {overview.catalog.price_snapshots.toLocaleString(LOCALE)}
                  </MetricRow>
                  <MetricRow label="Portfolios">
                    {overview.catalog.portfolios.toLocaleString(LOCALE)}
                  </MetricRow>
                  <MetricRow label="Owned holdings">
                    {overview.catalog.owned_holdings.toLocaleString(LOCALE)}
                  </MetricRow>
                  <MetricRow label="Owned copies">
                    {overview.catalog.owned_copies.toLocaleString(LOCALE)}
                  </MetricRow>
                </dl>
              </StatusTile>

              <StatusTile title="Portfolio value">
                {overview.valuation.as_of == null ? (
                  <p className="text-sm text-muted-foreground">Not yet valued.</p>
                ) : (
                  <dl className="space-y-0.5">
                    <MetricRow label="Market value">
                      {(() => {
                        const mv = parseDecimal(overview.valuation.market_value);
                        return mv == null ? "—" : formatUsd(mv);
                      })()}
                    </MetricRow>
                    <MetricRow label="As of">
                      {formatDayShort(overview.valuation.as_of)}
                    </MetricRow>
                    <MetricRow label="Portfolios">
                      {overview.valuation.portfolios_valued}
                    </MetricRow>
                    {overview.valuation.complete === false ? (
                      <p className="pt-1 text-xs text-flat">
                        Partial coverage — some holdings unpriced.
                      </p>
                    ) : null}
                  </dl>
                )}
              </StatusTile>

              <StatusTile title="Deployment">
                <dl className="space-y-0.5">
                  <MetricRow label="Version">
                    <span className="text-gold-700">{overview.app.version}</span>
                  </MetricRow>
                  <MetricRow label="Environment">{overview.app.environment}</MetricRow>
                  <MetricRow label="Uptime">
                    {formatUptime(overview.app.uptime_seconds)}
                  </MetricRow>
                  <MetricRow label="Server time">
                    {formatDateTimeUtc(overview.app.server_time)}
                  </MetricRow>
                </dl>
              </StatusTile>

              <InfraTile
                infra={infraQuery.data}
                error={infraQuery.isError && infraQuery.data === undefined}
              />
            </div>

            <RecentRuns runs={overview.recent_runs} />
          </div>
        ) : (
          <QueryErrorState
            title="Couldn't load status."
            onRetry={() => query.refetch()}
          />
        )}
      </div>
    </div>
  );
}
