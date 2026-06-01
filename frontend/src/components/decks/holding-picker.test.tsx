import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CollectionItemList } from "@/lib/api";
import { collectionItemsListOptions } from "@/lib/api";

import { HoldingPicker } from "./holding-picker";

vi.mock("@/lib/api", () => ({
  collectionItemsListOptions: vi.fn(),
}));

const holdingsOptions = vi.mocked(collectionItemsListOptions);

function makeHolding(overrides: Partial<CollectionItemList> = {}): CollectionItemList {
  return {
    id: 100,
    portfolio: 1,
    portfolio_name: "Yubel Deck",
    printing: 9,
    card_name: "Ash Blossom & Joyous Spring",
    set_code: "L5DD-ENC09",
    set_rarity: "Common",
    variant_label: null,
    condition: "near_mint",
    edition: "first",
    language: "en",
    storage_location: null,
    storage_location_name: null,
    quantity: 3,
    ...overrides,
  };
}

// Resolve search results for any non-empty term (the >=2-char gate is the component's
// `enabled`, so a sub-2-char term never reaches this).
function stubSearch(results: CollectionItemList[]) {
  holdingsOptions.mockImplementation((options) => {
    const search = options?.query?.search ?? "";
    return {
      queryKey: [{ _id: "collectionItemsList", search }],
      queryFn: async () => ({
        count: search ? results.length : 0,
        next: null,
        previous: null,
        results: search ? results : [],
      }),
    } as unknown as ReturnType<typeof collectionItemsListOptions>;
  });
}

function stubSearchError() {
  holdingsOptions.mockImplementation((options) => {
    const search = options?.query?.search ?? "";
    return {
      queryKey: [{ _id: "collectionItemsList", search }],
      queryFn: async () => {
        throw new Error("boom");
      },
    } as unknown as ReturnType<typeof collectionItemsListOptions>;
  });
}

function renderPicker(
  props: { onSelect?: (item: CollectionItemList) => void; onCancel?: () => void } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <HoldingPicker
        onSelect={props.onSelect ?? vi.fn()}
        onCancel={props.onCancel ?? vi.fn()}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  stubSearch([makeHolding()]);
});

describe("HoldingPicker", () => {
  it("prompts for at least two characters before searching", () => {
    renderPicker();
    expect(
      screen.getByText(/Type at least 2 characters/i),
    ).toBeInTheDocument();
  });

  it("does not enable the search query for a single character", async () => {
    const user = userEvent.setup();
    renderPicker();

    await user.type(screen.getByLabelText(/search your collection/i), "a");
    // The query factory may be invoked during render, but the query stays disabled
    // (no results render) until the debounced term clears the 2-char gate.
    expect(screen.getByText(/Type at least 2 characters/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Ash Blossom/i }),
    ).not.toBeInTheDocument();
  });

  it("searches by name and returns the chosen holding to onSelect", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderPicker({ onSelect });

    await user.type(screen.getByLabelText(/search your collection/i), "Ash");
    // findBy polls past the 300ms debounce.
    await user.click(await screen.findByRole("button", { name: /Ash Blossom/i }));

    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: 100, card_name: "Ash Blossom & Joyous Spring" }),
    );
  });

  it("includes a non-null variant label in the holding descriptor", async () => {
    stubSearch([makeHolding({ variant_label: "Alternate Art" })]);
    const user = userEvent.setup();
    renderPicker();

    await user.type(screen.getByLabelText(/search your collection/i), "Ash");
    const result = await screen.findByRole("button", { name: /Ash Blossom/i });
    expect(result).toHaveTextContent("Alternate Art");
  });

  it("renders an empty state when nothing matches", async () => {
    stubSearch([]);
    const user = userEvent.setup();
    renderPicker();

    await user.type(screen.getByLabelText(/search your collection/i), "Zzz");
    expect(await screen.findByText(/No held copies match/i)).toBeInTheDocument();
  });

  it("filters out zero-copy holdings (a deck only groups cards you hold)", async () => {
    stubSearch([makeHolding({ id: 1, quantity: 0 })]); // matches the name, but 0 copies
    const user = userEvent.setup();
    renderPicker();

    await user.type(screen.getByLabelText(/search your collection/i), "Ash");
    // The zero-copy holding is filtered out → no clickable result, just the empty state.
    expect(await screen.findByText(/No held copies match/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Ash Blossom/i }),
    ).not.toBeInTheDocument();
  });

  it("discloses when the search has more matches than the first page", async () => {
    holdingsOptions.mockImplementation((options) => {
      const search = options?.query?.search ?? "";
      return {
        queryKey: [{ _id: "collectionItemsList", search }],
        queryFn: async () => ({
          count: 250,
          next: "http://test/?page=2", // more pages exist than the picker shows
          previous: null,
          results: search ? [makeHolding()] : [],
        }),
      } as unknown as ReturnType<typeof collectionItemsListOptions>;
    });
    const user = userEvent.setup();
    renderPicker();

    await user.type(screen.getByLabelText(/search your collection/i), "Ash");
    await screen.findByRole("button", { name: /Ash Blossom/i });
    expect(
      screen.getByText(/Showing the first 100 matches/i),
    ).toBeInTheDocument();
  });

  it("renders an error state with a retry control", async () => {
    stubSearchError();
    const user = userEvent.setup();
    renderPicker();

    await user.type(screen.getByLabelText(/search your collection/i), "Ash");
    expect(
      await screen.findByText(/Couldn.t search your collection/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("does not keep the previous term's results clickable while a new search is in flight", async () => {
    // "Ash" resolves to a result; any later term's fetch hangs (stays in-flight) so we can
    // observe the transition. Without keepPreviousData the stale Ash row must be gone — a fast
    // typist must not be able to add a holding that no longer matches the search box.
    holdingsOptions.mockImplementation((options) => {
      const search = options?.query?.search ?? "";
      return {
        queryKey: [{ _id: "collectionItemsList", search }],
        queryFn: async () => {
          if (search === "Ash") {
            return { count: 1, next: null, previous: null, results: [makeHolding()] };
          }
          return new Promise(() => {}); // a later term: stays pending
        },
      } as unknown as ReturnType<typeof collectionItemsListOptions>;
    });
    const user = userEvent.setup();
    renderPicker();

    const input = screen.getByLabelText(/search your collection/i);
    await user.type(input, "Ash");
    await screen.findByRole("button", { name: /Ash Blossom/i });

    await user.clear(input);
    await user.type(input, "Blue");

    // The new term is fetching; the stale Ash row must not remain clickable.
    expect(await screen.findByText(/Searching/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Ash Blossom/i }),
    ).not.toBeInTheDocument();
  });

  it("calls onCancel from the Cancel button", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    renderPicker({ onCancel });

    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
