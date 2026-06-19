import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  CollectionItemList,
  PaginatedCollectionItemListList,
} from "@/lib/api";
import {
  collectionItemsListOptions,
  portfolioPortfoliosListOptions,
} from "@/lib/api";

import CollectionPage from "./page";

// The page is the consumer under test; the typed client is mocked so no real
// fetch fires. Only the two read-option factories the page calls are stubbed;
// types still resolve from the real module (vi.mock is runtime-only).
vi.mock("@/lib/api", () => ({
  collectionItemsListOptions: vi.fn(),
  portfolioPortfoliosListOptions: vi.fn(),
}));

const itemsOptions = vi.mocked(collectionItemsListOptions);
const portfoliosOptions = vi.mocked(portfolioPortfoliosListOptions);

// Bind the fixture envelope to the real generated contract so a serializer
// drift (renamed/removed field) surfaces as a TS error here, not just at
// runtime. makeItem already returns the real CollectionItemList row type.
type ItemsPage = PaginatedCollectionItemListList;

function makeItem(
  overrides: Partial<CollectionItemList> = {}
): CollectionItemList {
  return {
    id: 1,
    portfolio: 1,
    portfolio_name: "Main",
    printing: 1,
    card_name: "Blue-Eyes White Dragon",
    set_code: "LOB-001",
    set_rarity: "UR",
    variant_label: null,
    condition: "near_mint",
    edition: "first",
    language: "en",
    storage_location_name: null,
    quantity: 3,
    ...overrides,
  };
}

function stubItems(
  impl: (page: number, portfolio: number | null) => ItemsPage
) {
  itemsOptions.mockImplementation((options) => {
    const page = options?.query?.page ?? 1;
    const portfolio = options?.query?.portfolio ?? null;
    return {
      queryKey: [{ _id: "collectionItemsList", query: { page, portfolio } }],
      queryFn: async () => impl(page, portfolio),
    } as unknown as ReturnType<typeof collectionItemsListOptions>;
  });
}

// Stub keyed on the FULL query object (not just page/portfolio), so changing any
// facet produces a distinct queryKey and TanStack refetches — what the facet/search
// tests assert against.
function stubItemsFull(impl: (query: Record<string, unknown>) => ItemsPage) {
  itemsOptions.mockImplementation((options) => {
    const query = options?.query ?? {};
    return {
      queryKey: [{ _id: "collectionItemsList", query }],
      queryFn: async () => impl(query),
    } as unknown as ReturnType<typeof collectionItemsListOptions>;
  });
}

function stubPortfolios(results: Array<{ id: number; name: string }>) {
  portfoliosOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "portfolioPortfoliosList" }],
        queryFn: async () => ({
          count: results.length,
          next: null,
          previous: null,
          results,
        }),
      }) as unknown as ReturnType<typeof portfolioPortfoliosListOptions>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CollectionPage />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  stubPortfolios([]);
});

describe("CollectionPage", () => {
  it("shows a loading skeleton before data resolves", () => {
    stubItems(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    // queryFn is async, so the first synchronous render is still pending.
    expect(
      screen.getByRole("status", { name: /loading collection/i })
    ).toBeInTheDocument();
  });

  it("renders one row per holding with humanized fields and quantities", async () => {
    stubItems(() => ({
      count: 2,
      next: null,
      previous: null,
      results: [
        makeItem({ id: 1, card_name: "Blue-Eyes White Dragon", quantity: 3 }),
        makeItem({
          id: 2,
          card_name: "Dark Magician",
          set_code: "LOB-005",
          set_rarity: "SR",
          condition: "light_played",
          edition: "unlimited",
          quantity: 1,
        }),
      ],
    }));
    renderPage();

    expect(
      await screen.findByText("Blue-Eyes White Dragon")
    ).toBeInTheDocument();
    expect(screen.getByText("Dark Magician")).toBeInTheDocument();
    // enum code -> human label (query the CELL, not the like-named filter <option>)
    expect(screen.getByRole("cell", { name: "Near Mint" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Light Played" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1st" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Unlimited" })).toBeInTheDocument();
    // the headline derived field actually renders (quantity cells, not footer)
    expect(screen.getByRole("cell", { name: "3" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1" })).toBeInTheDocument();
    // single-page footer
    expect(screen.getByText(/Page 1 of 1/)).toBeInTheDocument();
    expect(screen.getByText(/2 items/)).toBeInTheDocument();
  });

  it("renders a friendly empty state and no pagination footer", async () => {
    stubItems(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    // Unfiltered-empty → the lit display-case EmptyState with an import CTA.
    expect(await screen.findByText(/The vault is empty/i)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /import a csv/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();
  });

  it("renders a first-load error with retry and no stranding back-control", async () => {
    stubItems(() => {
      throw new Error("403");
    });
    renderPage();

    expect(
      await screen.findByText(/Couldn.t load your collection/i)
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    // on page 1 there is nowhere to go back to
    expect(
      screen.queryByRole("button", { name: /back to page/i })
    ).not.toBeInTheDocument();
  });

  it("pages forward, refetching with the next page and flipping boundary states", async () => {
    stubItems((page) =>
      page === 1
        ? {
            count: 150,
            next: "http://test/?page=2",
            previous: null,
            results: [makeItem({ id: 1, card_name: "Card Page One" })],
          }
        : {
            count: 150,
            next: null,
            previous: "http://test/?page=1",
            results: [makeItem({ id: 2, card_name: "Card Page Two" })],
          }
    );
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Card Page One")).toBeInTheDocument();
    expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument();
    // page 1: at the lower boundary
    expect(screen.getByRole("button", { name: /prev/i })).toBeDisabled();
    const next = screen.getByRole("button", { name: /next/i });
    expect(next).toBeEnabled();

    await user.click(next);

    expect(await screen.findByText("Card Page Two")).toBeInTheDocument();
    expect(screen.getByText(/Page 2 of 2/)).toBeInTheDocument();
    expect(itemsOptions).toHaveBeenCalledWith({ query: { page: 2 } });
    // page 2 (last): boundaries flip
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /prev/i })).toBeEnabled();
  });

  it("ignores a second Next click while the page is still loading", async () => {
    const page2 = deferred<ItemsPage>();
    itemsOptions.mockImplementation((options) => {
      const page = options?.query?.page ?? 1;
      return {
        queryKey: [{ _id: "collectionItemsList", query: { page } }],
        queryFn: () =>
          page === 1
            ? Promise.resolve({
                count: 150,
                next: "http://test/?page=2",
                previous: null,
                results: [makeItem({ id: 1, card_name: "Card Page One" })],
              })
            : page2.promise,
      } as unknown as ReturnType<typeof collectionItemsListOptions>;
    });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Card Page One")).toBeInTheDocument();
    const next = screen.getByRole("button", { name: /next/i });
    await user.click(next); // page -> 2, fetch in flight (isPaging)
    await user.click(next); // guarded no-op while paging

    page2.resolve({
      count: 150,
      next: null,
      previous: "http://test/?page=1",
      results: [makeItem({ id: 2, card_name: "Card Page Two" })],
    });

    expect(await screen.findByText("Card Page Two")).toBeInTheDocument();
    expect(screen.getByText(/Page 2 of 2/)).toBeInTheDocument();
    // the double click must not have skipped to page 3
    const requestedPages = itemsOptions.mock.calls.map(
      (call) => call[0]?.query?.page
    );
    expect(requestedPages).not.toContain(3);
  });

  it("offers a back-control when a non-first page fails, instead of stranding", async () => {
    stubItems((page) => {
      if (page === 1) {
        return {
          count: 150,
          next: "http://test/?page=2",
          previous: null,
          results: [makeItem({ id: 1, card_name: "Card Page One" })],
        };
      }
      throw new Error("500");
    });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Card Page One")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /next/i }));

    expect(
      await screen.findByText(/Couldn.t load your collection/i)
    ).toBeInTheDocument();
    const back = screen.getByRole("button", { name: /back to page 1/i });
    await user.click(back);

    // back to a page known to exist, not stuck on the error card
    expect(await screen.findByText("Card Page One")).toBeInTheDocument();
  });

  it("filters by portfolio and resets to page 1", async () => {
    stubPortfolios([
      { id: 7, name: "Vintage" },
      { id: 9, name: "Modern" },
    ]);
    stubItems((page, portfolio) => {
      if (portfolio === 7) {
        return {
          count: 1,
          next: null,
          previous: null,
          results: [makeItem({ id: 99, card_name: "Vintage Card" })],
        };
      }
      return page === 1
        ? {
            count: 150,
            next: "http://test/?page=2",
            previous: null,
            results: [makeItem({ id: 1, card_name: "All Page One" })],
          }
        : {
            count: 150,
            next: null,
            previous: "http://test/?page=1",
            results: [makeItem({ id: 2, card_name: "All Page Two" })],
          };
    });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("All Page One")).toBeInTheDocument();
    // options only render once the portfolios query resolves (select enabled)
    await screen.findByRole("option", { name: "Vintage" });

    // advance to page 2 FIRST, so the reset-to-1 below is a real assertion
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText("All Page Two")).toBeInTheDocument();
    expect(screen.getByText(/Page 2 of 2/)).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText(/filter by portfolio/i),
      "7"
    );

    expect(await screen.findByText("Vintage Card")).toBeInTheDocument();
    expect(itemsOptions).toHaveBeenLastCalledWith({
      query: { page: 1, portfolio: 7 },
    });
  });

  it("shows the filtered-empty message when a filter matches no holdings", async () => {
    stubPortfolios([{ id: 7, name: "Vintage" }]);
    stubItems(() => ({ count: 0, next: null, previous: null, results: [] }));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("option", { name: "Vintage" });
    await user.selectOptions(
      screen.getByLabelText(/filter by portfolio/i),
      "7"
    );

    expect(
      await screen.findByText(/No holdings match these filters/i)
    ).toBeInTheDocument();
    // the generic message belongs to the no-filter branch
    expect(screen.queryByText(/No holdings yet/i)).not.toBeInTheDocument();
  });

  it("filters by the condition facet and resets to page 1", async () => {
    stubItemsFull((query) =>
      query.condition === "near_mint"
        ? {
            count: 1,
            next: null,
            previous: null,
            results: [makeItem({ id: 5, card_name: "Near-Mint Card" })],
          }
        : {
            count: 2,
            next: null,
            previous: null,
            results: [makeItem({ id: 1, card_name: "Some Card" })],
          }
    );
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Some Card")).toBeInTheDocument();
    await user.selectOptions(
      screen.getByLabelText(/filter by condition/i),
      "near_mint"
    );

    expect(await screen.findByText("Near-Mint Card")).toBeInTheDocument();
    expect(itemsOptions).toHaveBeenLastCalledWith({
      query: { page: 1, condition: "near_mint" },
    });
  });

  it("debounces the card-name search and resets to page 1", async () => {
    stubItemsFull((query) =>
      query.search === "blossom"
        ? {
            count: 1,
            next: null,
            previous: null,
            results: [makeItem({ id: 8, card_name: "Ash Blossom" })],
          }
        : {
            count: 2,
            next: null,
            previous: null,
            results: [makeItem({ id: 1, card_name: "Some Card" })],
          }
    );
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Some Card")).toBeInTheDocument();
    await user.type(screen.getByLabelText(/search by card name/i), "blossom");

    // The request only fires after the 300ms debounce settles.
    expect(
      await screen.findByText("Ash Blossom", undefined, { timeout: 2000 })
    ).toBeInTheDocument();
    expect(itemsOptions).toHaveBeenLastCalledWith({
      query: { page: 1, search: "blossom" },
    });
  });
});
