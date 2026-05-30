import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CardDetail as CardDetailType, PriceSnapshot } from "@/lib/api";
import { cardsCardsRetrieveOptions, pricingSnapshotsList } from "@/lib/api";

import { CardDetail } from "./card-detail";

// Stub next/link (no router context in unit tests) and the recharts chart (its
// jsdom rendering is covered in price-line-chart.test.tsx) — here we only assert
// the chart receives the right number of points via a data attribute.
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/charts/price-line-chart", () => ({
  PriceLineChart: ({ data }: { data: Array<unknown> }) => (
    <div data-testid="price-chart" data-points={data.length} />
  ),
}));

// The chart's price history is the only pricing surface on this view, fetched
// via the page-walking list endpoint. (The per-printing "latest price" column
// was dropped this slice — a single cross-edition "latest" is ill-defined when a
// printing prices in multiple editions; deferred to a deliberate design.)
vi.mock("@/lib/api", () => ({
  cardsCardsRetrieveOptions: vi.fn(),
  pricingSnapshotsList: vi.fn(),
}));

const retrieveOptions = vi.mocked(cardsCardsRetrieveOptions);
const listFn = vi.mocked(pricingSnapshotsList);

// --- fixtures -----------------------------------------------------------------

function makeSnapshot(overrides: Partial<PriceSnapshot>): PriceSnapshot {
  return {
    id: 0,
    printing: 11,
    edition: "unlimited",
    source: "tcgcsv",
    snapshot_date: "2026-05-11",
    market_price: "42.00",
    created_at: "2026-05-11T04:00:00Z",
    ...overrides,
  };
}

const CARD: CardDetailType = {
  id: 5,
  passcode: 46986414,
  name: "Dark Magician",
  printings_count: 2,
  printings: [
    {
      id: 11,
      card: 5,
      card_name: "Dark Magician",
      set_code: "LOB-005",
      set_rarity: "Ultra Rare",
      variant_label: null,
      set_name: "Legend of Blue Eyes White Dragon",
      is_multi_variant: false,
    },
    {
      id: 12,
      card: 5,
      card_name: "Dark Magician",
      set_code: "SDY-006",
      set_rarity: "Common",
      variant_label: null,
      set_name: "Starter Deck: Yugi",
      is_multi_variant: false,
    },
  ],
};

// Newest-first per the real API ordering; the component re-sorts ascending.
// Printing 11 (LOB-005): unlimited (05-11, 05-10) + first (05-09).
// Printing 12 (SDY-006): unlimited only (05-11).
const SNAPSHOTS: Record<number, PriceSnapshot[]> = {
  11: [
    makeSnapshot({ id: 1, printing: 11, edition: "unlimited", snapshot_date: "2026-05-11", market_price: "42.00" }),
    makeSnapshot({ id: 2, printing: 11, edition: "unlimited", snapshot_date: "2026-05-10", market_price: "40.00" }),
    makeSnapshot({ id: 3, printing: 11, edition: "first", snapshot_date: "2026-05-09", market_price: "100.00" }),
  ],
  12: [
    makeSnapshot({ id: 4, printing: 12, edition: "unlimited", snapshot_date: "2026-05-11", market_price: "1.20" }),
  ],
};

function stubCard(card: CardDetailType) {
  retrieveOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "cardsCardsRetrieve" }],
        queryFn: async () => card,
      }) as unknown as ReturnType<typeof cardsCardsRetrieveOptions>,
  );
}

// Single-page list keyed by printing — the common case (history < 100 rows).
function stubSinglePage(map: Record<number, PriceSnapshot[]>) {
  listFn.mockImplementation(((options: { query: { printing: number } }) =>
    Promise.resolve({
      data: {
        count: (map[options.query.printing] ?? []).length,
        next: null,
        previous: null,
        results: map[options.query.printing] ?? [],
      },
    })) as unknown as typeof pricingSnapshotsList);
}

function renderDetail(cardId = 5) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CardDetail cardId={cardId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  stubCard(CARD);
  stubSinglePage(SNAPSHOTS);
});

describe("CardDetail", () => {
  it("shows a loading skeleton while the card is fetching", () => {
    retrieveOptions.mockImplementation(
      () =>
        ({
          queryKey: [{ _id: "cardsCardsRetrieve" }],
          queryFn: () => new Promise(() => {}),
        }) as unknown as ReturnType<typeof cardsCardsRetrieveOptions>,
    );
    renderDetail();

    expect(
      screen.getByRole("status", { name: /loading card/i }),
    ).toBeInTheDocument();
  });

  it("renders the header, printings, the default edition, and the chart", async () => {
    renderDetail();

    expect(await screen.findByText("Dark Magician")).toBeInTheDocument();
    expect(screen.getByText(/2 printings/)).toBeInTheDocument();
    expect(screen.getByText("LOB-005")).toBeInTheDocument();
    expect(screen.getByText("SDY-006")).toBeInTheDocument();

    // Default edition = the one with the most-recent data (unlimited, 05-11).
    const editionSelect = screen.getByRole("combobox", { name: "Edition" });
    await waitFor(() => expect(editionSelect).toHaveValue("unlimited"));
    expect(screen.getByRole("option", { name: "Unlimited" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1st Edition" })).toBeInTheDocument();

    // Chart series = unlimited points sorted ascending (05-10, 05-11) = 2.
    await waitFor(() =>
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-points", "2"),
    );
  });

  it("refilters the chart when the edition changes", async () => {
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() =>
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-points", "2"),
    );

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Edition" }),
      "first",
    );

    // first edition: only LOB-005 @ 05-09 ($100) → 1 point.
    await waitFor(() =>
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-points", "1"),
    );
  });

  it("page-walks the price history across multiple pages (review C6)", async () => {
    // Two pages for printing 11: page 1 has 2 unlimited points + a next link,
    // page 2 has 1 more. The chart series must be the COMBINED 3 points.
    const page1 = [
      makeSnapshot({ id: 1, printing: 11, edition: "unlimited", snapshot_date: "2026-05-11", market_price: "42.00" }),
      makeSnapshot({ id: 2, printing: 11, edition: "unlimited", snapshot_date: "2026-05-10", market_price: "40.00" }),
    ];
    const page2 = [
      makeSnapshot({ id: 3, printing: 11, edition: "unlimited", snapshot_date: "2026-05-09", market_price: "38.00" }),
    ];
    listFn.mockImplementation(((options: {
      query: { printing: number; page?: number };
    }) => {
      const page = options.query.page ?? 1;
      return Promise.resolve(
        page === 1
          ? { data: { count: 3, next: "http://test/?page=2", previous: null, results: page1 } }
          : { data: { count: 3, next: null, previous: "http://test/?page=1", results: page2 } },
      );
    }) as unknown as typeof pricingSnapshotsList);

    renderDetail();

    await waitFor(() =>
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-points", "3"),
    );
    // page 2 was actually requested.
    const requestedPages = listFn.mock.calls.map(
      (call) => (call[0] as { query: { page?: number } }).query.page,
    );
    expect(requestedPages).toContain(2);
  });

  it("charts a different printing's history when one is selected", async () => {
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() =>
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-points", "2"),
    );

    await user.click(
      await screen.findByRole("radio", {
        name: /show price history for SDY-006/i,
      }),
    );

    // SDY-006 has one unlimited point; the selector now offers only Unlimited.
    await waitFor(() =>
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-points", "1"),
    );
    expect(
      screen.queryByRole("option", { name: "1st Edition" }),
    ).not.toBeInTheDocument();
  });

  it("auto-falls-back to an available edition when the selected one disappears on printing switch (review C9)", async () => {
    const user = userEvent.setup();
    renderDetail();

    // Select "first" on LOB-005 (which has it).
    await waitFor(() =>
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-points", "2"),
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Edition" }),
      "first",
    );
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Edition" })).toHaveValue("first"),
    );

    // Switch to SDY-006, which only has 'unlimited' — the stale 'first'
    // selection must fall back to 'unlimited', not strand on an empty chart.
    await user.click(
      await screen.findByRole("radio", {
        name: /show price history for SDY-006/i,
      }),
    );

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Edition" })).toHaveValue("unlimited"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("price-chart")).toHaveAttribute("data-points", "1"),
    );
  });

  it("shows an empty state for a card with no printings", async () => {
    stubCard({ ...CARD, printings_count: 0, printings: [] });
    renderDetail();

    expect(
      await screen.findByText(/No printings recorded/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("price-chart")).not.toBeInTheDocument();
  });

  it("shows an error card with retry when the card fails to load", async () => {
    retrieveOptions.mockImplementation(
      () =>
        ({
          queryKey: [{ _id: "cardsCardsRetrieve" }],
          queryFn: async () => {
            throw new Error("403");
          },
        }) as unknown as ReturnType<typeof cardsCardsRetrieveOptions>,
    );
    renderDetail();

    expect(
      await screen.findByText(/Couldn.t load this card/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows a dedicated error + retry when the price history fails to load (review C7)", async () => {
    // Card loads; the history page-walk for the selected printing rejects.
    listFn.mockImplementation((() =>
      Promise.reject(new Error("500"))) as unknown as typeof pricingSnapshotsList);

    renderDetail();

    expect(
      await screen.findByText(/Couldn.t load price history/i),
    ).toBeInTheDocument();
    // The card itself rendered (header present) — only the history errored.
    expect(screen.getByText("Dark Magician")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
