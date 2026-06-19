import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MoverRow, PaginatedMoverRowList } from "@/lib/api";
import { valuationMoversListOptions } from "@/lib/api";

import MoversPage from "./page";

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

vi.mock("@/lib/api", () => ({
  valuationMoversListOptions: vi.fn(),
}));

const moversOptions = vi.mocked(valuationMoversListOptions);

function makeMover(overrides: Partial<MoverRow> = {}): MoverRow {
  return {
    printing: 1,
    card_id: 1,
    card_name: "Ash Blossom & Joyous Spring",
    set_code: "L5DD-ENC09",
    set_rarity: "Common",
    variant_label: null,
    edition: "first",
    start_price: "10.00",
    end_price: "12.00",
    abs_change: "2.00",
    pct_change: 0.2,
    start_date: "2026-05-01",
    end_date: "2026-05-31",
    ...overrides,
  };
}

function stubMovers(impl: (page: number) => PaginatedMoverRowList) {
  moversOptions.mockImplementation((options) => {
    const page = options?.query?.page ?? 1;
    return {
      queryKey: [{ _id: "valuationMoversList", query: options?.query }],
      queryFn: async () => impl(page),
    } as unknown as ReturnType<typeof valuationMoversListOptions>;
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MoversPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MoversPage", () => {
  it("shows a loading skeleton before data resolves", () => {
    stubMovers(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(
      screen.getByRole("status", { name: /loading movers/i }),
    ).toBeInTheDocument();
  });

  it("queries with the default window and ordering on first load", () => {
    stubMovers(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(moversOptions).toHaveBeenCalledWith({
      query: { page: 1, window: 30, ordering: "-pct_change" },
    });
  });

  it("renders a row with a card link, edition, prices and signed deltas", async () => {
    stubMovers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeMover({ card_id: 5 })],
    }));
    renderPage();

    const link = await screen.findByRole("link", {
      name: "Ash Blossom & Joyous Spring",
    });
    expect(link).toHaveAttribute("href", "/cards/5");
    expect(screen.getByText("1st Edition")).toBeInTheDocument();
    expect(screen.getByText("$10.00")).toBeInTheDocument();
    expect(screen.getByText("$12.00")).toBeInTheDocument();
    expect(screen.getByText("+$2.00")).toBeInTheDocument();
    expect(screen.getByText("+20.0%")).toBeInTheDocument();
    expect(screen.getByText(/1 mover/)).toBeInTheDocument();
  });

  it("renders a loss with a negative signed delta", async () => {
    stubMovers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [
        makeMover({ end_price: "8.00", abs_change: "-2.00", pct_change: -0.2 }),
      ],
    }));
    renderPage();

    expect(await screen.findByText("-$2.00")).toBeInTheDocument();
    expect(screen.getByText("-20.0%")).toBeInTheDocument();
  });

  it("renders an em-dash for a null percent (sub-floor base), keeping the dollar move", async () => {
    stubMovers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [
        makeMover({ start_price: "0.50", end_price: "1.50", abs_change: "1.00", pct_change: null }),
      ],
    }));
    renderPage();

    expect(await screen.findByText("+$1.00")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders a friendly empty state and no pagination footer", async () => {
    stubMovers(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(await screen.findByText(/Nothing has moved/i)).toBeInTheDocument();
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();
  });

  it("renders a first-load error with retry and no stranding back-control", async () => {
    stubMovers(() => {
      throw new Error("403");
    });
    renderPage();

    expect(await screen.findByText(/Couldn.t load movers/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /back to page/i }),
    ).not.toBeInTheDocument();
  });

  it("pages forward, requesting the next page with window + ordering preserved", async () => {
    stubMovers((page) =>
      page === 1
        ? {
            count: 150,
            next: "http://test/?page=2",
            previous: null,
            results: [makeMover({ card_id: 1, card_name: "Mover Page One" })],
          }
        : {
            count: 150,
            next: null,
            previous: "http://test/?page=1",
            results: [makeMover({ card_id: 2, card_name: "Mover Page Two" })],
          },
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: "Mover Page One" });
    await user.click(screen.getByRole("button", { name: /next/i }));

    await screen.findByRole("link", { name: "Mover Page Two" });
    expect(moversOptions).toHaveBeenCalledWith({
      query: { page: 2, window: 30, ordering: "-pct_change" },
    });
  });

  it("changes the window, requesting the param and resetting to page 1", async () => {
    stubMovers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeMover()],
    }));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: /Ash Blossom/ });
    await user.selectOptions(
      screen.getByRole("combobox", { name: /lookback window/i }),
      "7",
    );

    expect(moversOptions).toHaveBeenCalledWith({
      query: { page: 1, window: 7, ordering: "-pct_change" },
    });
  });

  it("toggles percent sort direction, resetting to page 1", async () => {
    stubMovers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeMover()],
    }));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: /Ash Blossom/ });
    // Default is -pct_change (descending). Clicking the % header toggles ascending.
    await user.click(screen.getByRole("button", { name: /sort by % change/i }));

    expect(moversOptions).toHaveBeenCalledWith({
      query: { page: 1, window: 30, ordering: "pct_change" },
    });
  });

  it("sorts by dollar change when the $ header is clicked", async () => {
    stubMovers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeMover()],
    }));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: /Ash Blossom/ });
    await user.click(screen.getByRole("button", { name: /sort by \$ change/i }));

    expect(moversOptions).toHaveBeenCalledWith({
      query: { page: 1, window: 30, ordering: "-abs_change" },
    });
  });

  it("marks the active sort column with aria-sort on its header cell", async () => {
    stubMovers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeMover()],
    }));
    renderPage();

    await screen.findByRole("link", { name: /Ash Blossom/ });
    const headers = screen.getAllByRole("columnheader");
    const pctHeader = headers.find((h) => h.textContent?.includes("% Change"));
    const absHeader = headers.find((h) => h.textContent?.includes("$ Change"));
    const cardHeader = headers.find((h) => h.textContent === "Card");

    // Default ordering is -pct_change → the % column is the active descending sort.
    expect(pctHeader).toHaveAttribute("aria-sort", "descending");
    // The other sortable column is sortable-but-inactive.
    expect(absHeader).toHaveAttribute("aria-sort", "none");
    // A non-sortable column carries no aria-sort at all.
    expect(cardHeader).not.toHaveAttribute("aria-sort");
  });
});
