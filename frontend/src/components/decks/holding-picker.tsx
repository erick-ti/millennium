"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { type CollectionItemList, collectionItemsListOptions } from "@/lib/api";
import { Button } from "@/components/ui/button";

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

// Local label copies (the collection-page / alerts-page convention — these maps
// are duplicated per consumer rather than shared, to keep each view self-contained).
// Keyed loosely (`string`) because the holding's enum values arrive as plain strings.
const CONDITION_LABELS: Record<string, string> = {
  mint: "Mint",
  near_mint: "Near Mint",
  excellent: "Excellent",
  good: "Good",
  light_played: "Light Played",
  played: "Played",
  poor: "Poor",
};
const EDITION_LABELS: Record<string, string> = {
  first: "1st",
  unlimited: "Unlimited",
  limited: "Limited",
};
const LANGUAGE_LABELS: Record<string, string> = {
  en: "EN",
  fr: "FR",
  de: "DE",
  it: "IT",
  es: "ES",
  pt: "PT",
  ja: "JA",
  ko: "KO",
};

/** A one-line disambiguating descriptor for an owned holding. */
function holdingDescriptor(item: CollectionItemList): string {
  const parts = [item.set_code, item.set_rarity];
  if (item.variant_label) parts.push(item.variant_label);
  parts.push(CONDITION_LABELS[item.condition] ?? item.condition);
  parts.push(EDITION_LABELS[item.edition] ?? item.edition);
  parts.push(LANGUAGE_LABELS[item.language] ?? item.language);
  parts.push(item.portfolio_name);
  return parts.join(" · ");
}

/**
 * One-step picker for adding an OWNED holding to a deck: search the collection by
 * card name (the existing `?search=` icontains facet on `/api/collection/items/`)
 * and pick a `CollectionItem`. Searching CollectionItems IS searching owned cards
 * by definition (DECISIONS 2026-05-18), so OWNED-only needs no extra predicate.
 * Adapted from the import-review `PrintingPicker` (debounced search → scrollable
 * result list → `onSelect`); a duplicate add is surfaced by the caller's 409, so the
 * picker does not pre-filter members (the member set spans pages — incomplete
 * disabling would be worse than none).
 */
export function HoldingPicker({
  onSelect,
  onCancel,
  isSubmitting = false,
}: {
  onSelect: (item: CollectionItemList) => void;
  onCancel: () => void;
  isSubmitting?: boolean;
}) {
  const [term, setTerm] = useState("");
  const debouncedTerm = useDebounced(term.trim(), 300);
  const searchEnabled = debouncedTerm.length >= 2;
  const searchRef = useRef<HTMLInputElement>(null);

  // Focus the search input on mount (the picker opens via a button click, which would
  // otherwise leave focus on that button — the slice-3 focus rule).
  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  const holdingsQuery = useQuery({
    ...collectionItemsListOptions({ query: { search: debouncedTerm, page: 1 } }),
    enabled: searchEnabled,
    // No keepPreviousData here (unlike the paged tables): on a term change the prior term's
    // results must NOT linger as clickable rows, or a fast typist could add a holding that no
    // longer matches the search box (Codex adversarial review 2026-05-31). A term change shows
    // "Searching…" until the new results arrive. staleTime still caches an identical re-search.
    staleTime: 60 * 1000,
  });

  // Only show holdings you actually hold (quantity > 0) — a deck groups held cards, and the
  // backend rejects a zero-copy add anyway (Codex adversarial review 2026-05-31).
  const holdings = (holdingsQuery.data?.results ?? []).filter((item) => item.quantity > 0);
  // The collection search paginates at 100 (card-name-only), and this picker shows only page 1.
  // Disclose when there are more matches than shown so a holding on a later page isn't silently
  // unreachable — the project's no-silent-caps principle (Codex adversarial review 2026-05-31).
  const hasMoreMatches = Boolean(holdingsQuery.data?.next);

  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">Add a holding to this deck</h3>
        <Button size="xs" variant="ghost" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
      </div>

      <div className="mt-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">Search your collection by card name</span>
          <input
            ref={searchRef}
            type="search"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            placeholder="e.g. Ash Blossom"
            aria-label="Search your collection by card name"
            className="h-9 rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
        </label>

        <div className="mt-3">
          {!searchEnabled ? (
            <p className="text-sm text-muted-foreground">
              Type at least 2 characters to search your collection.
            </p>
          ) : holdingsQuery.isPending ? (
            <p className="text-sm text-muted-foreground">Searching…</p>
          ) : holdingsQuery.isError ? (
            <p className="text-sm text-destructive">
              Couldn&apos;t search your collection.{" "}
              <button
                type="button"
                onClick={() => holdingsQuery.refetch()}
                className="underline underline-offset-4"
              >
                Retry
              </button>
            </p>
          ) : holdings.length === 0 ? (
            // No matches, OR page 1 matched only zero-copy holdings (filtered out).
            <p className="text-sm text-muted-foreground">
              No held copies match that name.
            </p>
          ) : (
            <>
              <ul className="max-h-60 divide-y divide-border overflow-y-auto rounded-md border border-border">
                {holdings.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      disabled={isSubmitting}
                      onClick={() => onSelect(item)}
                      className="flex w-full flex-col gap-0.5 px-3 py-2 text-left text-sm hover:bg-muted disabled:opacity-50"
                    >
                      <span className="font-medium text-foreground">
                        {item.card_name}
                        <span className="ml-2 text-xs font-normal text-muted-foreground tabular-nums">
                          {item.quantity}×
                        </span>
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {holdingDescriptor(item)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              {hasMoreMatches ? (
                <p className="mt-2 text-xs text-muted-foreground">
                  Showing the first 100 matches. Refine your search to narrow the list.
                </p>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
