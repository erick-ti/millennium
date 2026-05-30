"use client";

import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  cardsCardsListOptions,
  cardsPrintingsListOptions,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Pill } from "@/components/imports/status";

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/**
 * Two-step picker for the import-review override: search a card by name (the
 * slice-6 `?search=` filter), then choose one of that card's printings. Returns
 * the chosen printing id to the caller, which fires the override mutation. Built
 * inline (no Dialog primitive exists); a card has at most a handful of printings,
 * so page 1 of each query is shown without pagination.
 */
export function PrintingPicker({
  onSelect,
  onCancel,
  isSubmitting = false,
}: {
  onSelect: (printingId: number) => void;
  onCancel: () => void;
  isSubmitting?: boolean;
}) {
  const [term, setTerm] = useState("");
  const [selectedCardId, setSelectedCardId] = useState<number | null>(null);
  const debouncedTerm = useDebounced(term.trim(), 300);
  const searchEnabled = debouncedTerm.length >= 2;

  const searchRef = useRef<HTMLInputElement>(null);
  const backRef = useRef<HTMLButtonElement>(null);

  // Each step swap unmounts the control the user just clicked (a card button → printings
  // view; "Back" → search view), which would otherwise strand keyboard focus on <body>
  // (the slice-3 focus rule). Move focus deliberately: search input on the search step,
  // the Back button on the printings step. Runs on mount too (focuses the search input).
  useEffect(() => {
    if (selectedCardId == null) {
      searchRef.current?.focus();
    } else {
      backRef.current?.focus();
    }
  }, [selectedCardId]);

  const cardsQuery = useQuery({
    ...cardsCardsListOptions({ query: { search: debouncedTerm, page: 1 } }),
    enabled: searchEnabled && selectedCardId == null,
    placeholderData: keepPreviousData,
    staleTime: 60 * 1000,
  });

  const printingsQuery = useQuery({
    ...cardsPrintingsListOptions({ query: { card: selectedCardId ?? 0 } }),
    enabled: selectedCardId != null,
    staleTime: 60 * 1000,
  });

  const cards = cardsQuery.data?.results ?? [];
  const printings = printingsQuery.data?.results ?? [];

  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">Choose the correct printing</h3>
        <Button size="xs" variant="ghost" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
      </div>

      {selectedCardId == null ? (
        <div className="mt-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted-foreground">Search a card by name</span>
            <input
              ref={searchRef}
              type="search"
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder="e.g. Ash Blossom"
              aria-label="Search a card by name"
              className="h-9 rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            />
          </label>

          <div className="mt-3">
            {!searchEnabled ? (
              <p className="text-sm text-muted-foreground">
                Type at least 2 characters to search.
              </p>
            ) : cardsQuery.isPending ? (
              <p className="text-sm text-muted-foreground">Searching…</p>
            ) : cardsQuery.isError ? (
              <p className="text-sm text-destructive">
                Couldn&apos;t search cards.{" "}
                <button
                  type="button"
                  onClick={() => cardsQuery.refetch()}
                  className="underline underline-offset-4"
                >
                  Retry
                </button>
              </p>
            ) : cards.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No cards match that name.
              </p>
            ) : (
              <ul className="max-h-60 divide-y divide-border overflow-y-auto rounded-md border border-border">
                {cards.map((card) => (
                  <li key={card.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedCardId(card.id)}
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-muted"
                    >
                      <span className="font-medium text-foreground">{card.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {card.printings_count}{" "}
                        {card.printings_count === 1 ? "printing" : "printings"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <button
            ref={backRef}
            type="button"
            onClick={() => setSelectedCardId(null)}
            className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            disabled={isSubmitting}
          >
            ‹ Back to results
          </button>

          <div className="mt-3">
            {printingsQuery.isPending ? (
              <p className="text-sm text-muted-foreground">Loading printings…</p>
            ) : printingsQuery.isError ? (
              <p className="text-sm text-destructive">
                Couldn&apos;t load printings.{" "}
                <button
                  type="button"
                  onClick={() => printingsQuery.refetch()}
                  className="underline underline-offset-4"
                >
                  Retry
                </button>
              </p>
            ) : printings.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                This card has no printings recorded.
              </p>
            ) : (
              <ul className="max-h-60 divide-y divide-border overflow-y-auto rounded-md border border-border">
                {printings.map((printing) => (
                  <li key={printing.id}>
                    <button
                      type="button"
                      disabled={isSubmitting}
                      onClick={() => onSelect(printing.id)}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted disabled:opacity-50"
                    >
                      <span className="font-medium text-foreground">
                        {printing.set_code}
                      </span>
                      <span className="text-muted-foreground">
                        {printing.set_rarity}
                      </span>
                      {printing.variant_label ? (
                        <span className="text-muted-foreground">
                          · {printing.variant_label}
                        </span>
                      ) : null}
                      {printing.is_multi_variant ? (
                        <Pill tone="amber">multi-variant</Pill>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
