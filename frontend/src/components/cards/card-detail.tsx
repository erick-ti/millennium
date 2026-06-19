"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  type EditionEnum,
  type PriceSnapshot,
  cardsCardsRetrieveOptions,
  pricingSnapshotsList,
} from "@/lib/api";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/ui/page-header";
import { QueryErrorState } from "@/components/ui/query-error-state";
import {
  PriceLineChart,
  type PricePoint,
} from "@/components/charts/price-line-chart";
import { DetailSkeleton } from "@/components/cards/detail-skeleton";
import { formatDayShort, parseDecimal } from "@/lib/format";

const EDITION_LABELS: Record<EditionEnum, string> = {
  first: "1st Edition",
  unlimited: "Unlimited",
  limited: "Limited",
};

// Deterministic tiebreak when two editions share the same most-recent
// snapshot_date (the common case — the daily reconcile stamps every edition
// with the same date). Without it the default edition would depend on backend
// intra-date row order (review K6). Lower = preferred as the default.
const EDITION_PRIORITY: Record<EditionEnum, number> = {
  first: 0,
  unlimited: 1,
  limited: 2,
};

// DRF paginates at PAGE_SIZE=100 and exposes no page_size override; a full price
// history needs page-walking. Cap the walk well above any real series (50 pages
// = 5,000 daily snapshots) as a runaway backstop.
const MAX_HISTORY_PAGES = 50;

type PriceHistory = { snapshots: PriceSnapshot[]; truncated: boolean };

/**
 * Page-walk every snapshot for one printing (all editions), following DRF's
 * `next` link. The list is newest-first and capped at 100 rows/page, so the
 * full series is assembled across pages here; the caller filters by edition and
 * re-sorts ascending for the chart. We fetch all editions in ONE walk so the
 * edition selector can be populated from the same data without a second query;
 * `truncated` is true if the page cap was hit with more rows still available —
 * surfaced rather than silently dropped (the project's "no silent caps" rule).
 */
async function fetchAllSnapshots(
  printing: number,
  signal: AbortSignal,
): Promise<PriceHistory> {
  const snapshots: PriceSnapshot[] = [];
  let truncated = false;
  for (let page = 1; page <= MAX_HISTORY_PAGES; page += 1) {
    const { data } = await pricingSnapshotsList({
      query: { printing, page },
      signal,
      throwOnError: true,
    });
    snapshots.push(...(data?.results ?? []));
    if (!data?.next) {
      break;
    }
    if (page === MAX_HISTORY_PAGES) {
      // More rows exist but we've hit the page cap; the oldest history is
      // dropped. Flag it so the UI can say so instead of looking complete.
      truncated = true;
    }
  }
  return { snapshots, truncated };
}

export function CardDetail({ cardId }: { cardId: number }) {
  const cardQuery = useQuery(cardsCardsRetrieveOptions({ path: { id: cardId } }));
  const printings = cardQuery.data?.printings ?? [];

  const [selectedPrintingId, setSelectedPrintingId] = useState<number | null>(
    null,
  );
  const [selectedEdition, setSelectedEdition] = useState<EditionEnum | null>(
    null,
  );

  // Effective selection is DERIVED (no effects): default to the first printing
  // until the user picks one. Reverts cleanly if the card data changes.
  const effectivePrintingId = selectedPrintingId ?? printings[0]?.id ?? null;

  const historyQuery = useQuery({
    queryKey: ["cardPriceHistory", effectivePrintingId],
    queryFn: ({ signal }) =>
      fetchAllSnapshots(effectivePrintingId as number, signal),
    enabled: effectivePrintingId != null,
    placeholderData: keepPreviousData,
    staleTime: 60 * 1000,
  });
  const snapshots = useMemo(
    () => historyQuery.data?.snapshots ?? [],
    [historyQuery.data],
  );
  const historyTruncated = historyQuery.data?.truncated ?? false;

  // Editions that actually have data for the selected printing, ordered by
  // most-recent snapshot then a fixed edition priority on a date tie (review
  // K6), so the default lands on a populated, deterministic series.
  const availableEditions = useMemo(() => {
    const latestByEdition = new Map<EditionEnum, string>();
    for (const snap of snapshots) {
      const prev = latestByEdition.get(snap.edition);
      if (prev == null || snap.snapshot_date > prev) {
        latestByEdition.set(snap.edition, snap.snapshot_date);
      }
    }
    return [...latestByEdition.entries()]
      .sort((a, b) => {
        if (a[1] !== b[1]) return a[1] < b[1] ? 1 : -1; // newer date first
        return EDITION_PRIORITY[a[0]] - EDITION_PRIORITY[b[0]]; // tie → priority
      })
      .map(([edition]) => edition);
  }, [snapshots]);

  const effectiveEdition: EditionEnum | null =
    selectedEdition != null && availableEditions.includes(selectedEdition)
      ? selectedEdition
      : (availableEditions[0] ?? null);

  // Chart series: the selected edition's points, parsed and sorted ascending.
  // A null market_price is a gap, not zero, so those points drop out.
  const series: PricePoint[] = useMemo(() => {
    if (effectiveEdition == null) {
      return [];
    }
    return snapshots
      .filter((snap) => snap.edition === effectiveEdition)
      .map((snap) => ({
        date: snap.snapshot_date,
        price: parseDecimal(snap.market_price),
      }))
      .filter((point): point is PricePoint => point.price != null)
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  }, [snapshots, effectiveEdition]);

  if (cardQuery.isPending) {
    return <DetailSkeleton />;
  }

  if (cardQuery.isError || cardQuery.data == null) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-10">
        <BackLink />
        <div className="mt-4">
          <QueryErrorState
            title="Couldn't load this card."
            onRetry={() => cardQuery.refetch()}
          />
        </div>
      </div>
    );
  }

  const card = cardQuery.data;
  const chartLabel =
    effectiveEdition != null && series.length > 0
      ? `${EDITION_LABELS[effectiveEdition]} market price, ${formatDayShort(
          series[0].date,
        )} to ${formatDayShort(series[series.length - 1].date)}, ${
          series.length
        } points`
      : "Price history";

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <BackLink />
      <PageHeader
        className="mt-3"
        kicker="CATALOG"
        title={card.name}
        subtitle={
          <>
            {card.archetype != null ? `${card.archetype} · ` : ""}
            {printings.length}{" "}
            {printings.length === 1 ? "printing" : "printings"}
            {card.passcode != null ? ` · passcode ${card.passcode}` : ""}
          </>
        }
      />

      {printings.length === 0 ? (
        <p className="vitrine rounded-lg p-6 text-sm text-bone-muted">
          No printings recorded for this card yet.
        </p>
      ) : (
        <>
          <section>
            <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
              Printings
            </h2>
            <p className="mt-1.5 text-sm text-bone-muted">
              Select a printing to chart its price history below.
            </p>
            <div className="mt-3 overflow-hidden rounded-lg border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">
                      <span className="sr-only">Select</span>
                    </TableHead>
                    <TableHead>Set</TableHead>
                    <TableHead>Rarity</TableHead>
                    <TableHead>Variant</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {printings.map((printing) => {
                    const isSelected = printing.id === effectivePrintingId;
                    return (
                      <TableRow
                        key={printing.id}
                        data-state={isSelected ? "selected" : undefined}
                      >
                        <TableCell>
                          <input
                            type="radio"
                            name="printing"
                            checked={isSelected}
                            onChange={() => setSelectedPrintingId(printing.id)}
                            aria-label={`Show price history for ${printing.set_code} ${printing.set_rarity}`}
                            className="accent-primary"
                          />
                        </TableCell>
                        <TableCell className="font-medium text-bone">
                          {printing.set_code}
                          {printing.is_multi_variant ? (
                            <span
                              className="ml-2 rounded bg-vault-800 px-1.5 py-0.5 font-terminal text-[0.65rem] font-normal uppercase tracking-[0.08em] text-bone-muted"
                              title="This generic printing covers multiple sellable variants."
                            >
                              multi-variant
                            </span>
                          ) : null}
                        </TableCell>
                        <TableCell>{printing.set_rarity}</TableCell>
                        <TableCell>{printing.variant_label ?? "—"}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </section>

          <section className="mt-10">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-terminal text-xs uppercase tracking-[0.16em] text-gold-700">
                Price history
              </h2>
              <label className="flex items-center gap-2 text-sm text-bone-muted">
                <span>Edition</span>
                <select
                  aria-label="Edition"
                  value={effectiveEdition ?? ""}
                  disabled={availableEditions.length === 0}
                  onChange={(event) =>
                    setSelectedEdition(event.target.value as EditionEnum)
                  }
                  className="h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
                >
                  {availableEditions.length === 0 ? (
                    <option value="">—</option>
                  ) : (
                    availableEditions.map((edition) => (
                      <option key={edition} value={edition}>
                        {EDITION_LABELS[edition]}
                      </option>
                    ))
                  )}
                </select>
              </label>
            </div>

            <div className="mt-4">
              {historyQuery.isPending ? (
                <div
                  role="status"
                  aria-busy="true"
                  aria-label="Loading price history"
                  className="vitrine h-72 animate-pulse rounded-lg"
                >
                  <span className="sr-only">Loading price history…</span>
                </div>
              ) : historyQuery.isError ? (
                <QueryErrorState
                  title="Couldn't load price history."
                  onRetry={() => historyQuery.refetch()}
                />
              ) : series.length === 0 ? (
                <p className="vitrine rounded-lg p-6 text-sm text-bone-muted">
                  {effectiveEdition != null
                    ? `No price history for the ${EDITION_LABELS[effectiveEdition]} edition yet.`
                    : "No price history for this printing yet."}
                </p>
              ) : (
                <div
                  aria-busy={historyQuery.isPlaceholderData}
                  className={`vitrine rounded-lg p-4 sm:p-6 ${
                    historyQuery.isPlaceholderData
                      ? "opacity-60 transition-opacity"
                      : ""
                  }`}
                >
                  <PriceLineChart data={series} label={chartLabel} />
                  <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 font-terminal text-[0.7rem] text-bone-muted">
                    <span className="flex items-center gap-2">
                      <span className="inline-block h-px w-5 bg-gold-700" />
                      {effectiveEdition != null
                        ? `${EDITION_LABELS[effectiveEdition]} market price`
                        : "Market price"}
                    </span>
                    <span className="ml-auto hidden sm:inline">
                      Tab to the chart for a screen-reader data table.
                    </span>
                  </div>
                  {historyTruncated ? (
                    <p className="mt-2 font-terminal text-[0.7rem] text-bone-muted">
                      Showing the most recent price points; older history was
                      truncated.
                    </p>
                  ) : null}
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/cards"
      className="font-terminal text-xs uppercase tracking-[0.12em] text-gold-700 transition-colors hover:text-gold-500"
    >
      ← Cards
    </Link>
  );
}
