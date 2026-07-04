import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Portfolio, PortfolioValueSnapshot } from "@/lib/api";
import {
  portfolioPortfoliosRetrieveOptions,
  portfolioSnapshotsList,
} from "@/lib/api";

import { PortfolioDetail } from "./portfolio-detail";

// Stub next/link, and the recharts chart (its jsdom rendering is covered in
// price-line-chart.test.tsx); here we assert it receives the right point count
// and the portfolio series label.
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
  PriceLineChart: ({
    data,
    label,
    seriesLabel,
  }: {
    data: Array<{ complete?: boolean }>;
    label?: string;
    seriesLabel?: string;
  }) => (
    <div
      data-testid="value-chart"
      data-points={data.length}
      data-partial-points={
        data.filter((point) => point.complete === false).length
      }
      data-label={label}
      data-series-label={seriesLabel}
    />
  ),
}));

vi.mock("@/lib/api", () => ({
  portfolioPortfoliosRetrieveOptions: vi.fn(),
  portfolioSnapshotsList: vi.fn(),
}));

const retrieveOptions = vi.mocked(portfolioPortfoliosRetrieveOptions);
const listFn = vi.mocked(portfolioSnapshotsList);

// --- fixtures -----------------------------------------------------------------

function makeSnapshot(
  overrides: Partial<PortfolioValueSnapshot>,
): PortfolioValueSnapshot {
  return {
    id: 0,
    portfolio: 3,
    snapshot_date: "2026-05-29",
    market_value: "1240.50",
    liquidation_value: "1054.40",
    cost_basis: "980.00",
    unrealized_gain: "260.50",
    total_card_count: 15,
    priced_card_count: 15,
    costed_card_count: 15,
    market_value_complete: true,
    cost_basis_complete: true,
    is_complete: true,
    valuation_method: "tcgcsv-latest",
    valuation_version: 1,
    created_at: "2026-05-29T04:00:00Z",
    ...overrides,
  };
}

const PORTFOLIO: Portfolio = {
  id: 3,
  name: "Yubel Deck",
  latest_snapshot: makeSnapshot({ id: 100, snapshot_date: "2026-05-29" }),
};

// Newest-first per the real API ordering; the component re-sorts ascending.
const HISTORY: PortfolioValueSnapshot[] = [
  makeSnapshot({ id: 3, snapshot_date: "2026-05-29", market_value: "1240.50" }),
  makeSnapshot({ id: 2, snapshot_date: "2026-05-28", market_value: "1200.00" }),
  makeSnapshot({ id: 1, snapshot_date: "2026-05-27", market_value: "1100.00" }),
];

function stubPortfolio(portfolio: Portfolio) {
  retrieveOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "portfolioPortfoliosRetrieve" }],
        queryFn: async () => portfolio,
      }) as unknown as ReturnType<typeof portfolioPortfoliosRetrieveOptions>,
  );
}

function stubSinglePage(rows: PortfolioValueSnapshot[]) {
  listFn.mockImplementation((() =>
    Promise.resolve({
      data: {
        count: rows.length,
        next: null,
        previous: null,
        results: rows,
      },
    })) as unknown as typeof portfolioSnapshotsList);
}

function renderDetail(portfolioId = 3) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PortfolioDetail portfolioId={portfolioId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  stubPortfolio(PORTFOLIO);
  stubSinglePage(HISTORY);
});

describe("PortfolioDetail", () => {
  it("shows a loading skeleton while the portfolio is fetching", () => {
    retrieveOptions.mockImplementation(
      () =>
        ({
          queryKey: [{ _id: "portfolioPortfoliosRetrieve" }],
          queryFn: () => new Promise(() => {}),
        }) as unknown as ReturnType<typeof portfolioPortfoliosRetrieveOptions>,
    );
    renderDetail();

    expect(
      screen.getByRole("status", { name: /loading portfolio/i }),
    ).toBeInTheDocument();
  });

  it("renders the header, summary metrics, and the value-history chart", async () => {
    renderDetail();

    expect(await screen.findByText("Yubel Deck")).toBeInTheDocument();
    // Summary market value.
    expect(screen.getByText("$1,240.50")).toBeInTheDocument();
    expect(screen.getByText(/\+\$260\.50/)).toBeInTheDocument();

    // Chart series = all 3 snapshots, with the portfolio series label.
    await waitFor(() =>
      expect(screen.getByTestId("value-chart")).toHaveAttribute(
        "data-points",
        "3",
      ),
    );
    expect(screen.getByTestId("value-chart")).toHaveAttribute(
      "data-series-label",
      "Portfolio value",
    );
    // The assembled accessible label: name + ascending date range + count.
    // HISTORY is newest-first (05-29, 05-28, 05-27); the series sorts ascending,
    // so the range reads oldest→newest.
    expect(screen.getByTestId("value-chart")).toHaveAttribute(
      "data-label",
      "Yubel Deck value, May 27 to May 29, 3 points",
    );
  });

  it("shows a 'not yet valued' header when the portfolio has no snapshot", async () => {
    stubPortfolio({ ...PORTFOLIO, latest_snapshot: null });
    renderDetail();

    expect(await screen.findByText("Yubel Deck")).toBeInTheDocument();
    // Distinctive to the metrics panel (the header meta line also reads "Not
    // yet valued"); asserts the NULL-safe summary path, not a row of $0s.
    expect(
      screen.getByText(/records the first snapshot/i),
    ).toBeInTheDocument();
  });

  it("page-walks the value history across multiple pages", async () => {
    const page1 = [
      makeSnapshot({ id: 3, snapshot_date: "2026-05-29", market_value: "1240.50" }),
      makeSnapshot({ id: 2, snapshot_date: "2026-05-28", market_value: "1200.00" }),
    ];
    const page2 = [
      makeSnapshot({ id: 1, snapshot_date: "2026-05-27", market_value: "1100.00" }),
    ];
    listFn.mockImplementation(((options: {
      query: { portfolio: number; page?: number };
    }) => {
      const page = options.query.page ?? 1;
      return Promise.resolve(
        page === 1
          ? { data: { count: 3, next: "http://test/?page=2", previous: null, results: page1 } }
          : { data: { count: 3, next: null, previous: "http://test/?page=1", results: page2 } },
      );
    }) as unknown as typeof portfolioSnapshotsList);

    renderDetail();

    await waitFor(() =>
      expect(screen.getByTestId("value-chart")).toHaveAttribute(
        "data-points",
        "3",
      ),
    );
    const requestedPages = listFn.mock.calls.map(
      (call) => (call[0] as { query: { page?: number } }).query.page,
    );
    expect(requestedPages).toContain(2);
  });

  it("marks partial-coverage snapshots in the series and notes them below the chart", async () => {
    // market_value on a partial day sums only the priced subset, so the chart
    // must distinguish those points from complete ones.
    stubSinglePage([
      makeSnapshot({
        id: 3,
        snapshot_date: "2026-05-29",
        market_value: "1240.50",
        market_value_complete: true,
      }),
      makeSnapshot({
        id: 2,
        snapshot_date: "2026-05-28",
        market_value: "800.00",
        market_value_complete: false,
        is_complete: false,
        priced_card_count: 9,
        total_card_count: 15,
      }),
      makeSnapshot({
        id: 1,
        snapshot_date: "2026-05-27",
        market_value: "1100.00",
        market_value_complete: true,
      }),
    ]);
    renderDetail();

    await waitFor(() =>
      expect(screen.getByTestId("value-chart")).toHaveAttribute(
        "data-partial-points",
        "1",
      ),
    );
    expect(
      screen.getByText(/partial pricing coverage/i),
    ).toBeInTheDocument();
  });

  it("surfaces a truncation notice when the page cap is hit with more rows available", async () => {
    // Every page reports a `next` link, so the walk reaches MAX_HISTORY_PAGES
    // with more rows still available → truncated=true (the "no silent caps"
    // rule). One row per page keeps the fixture cheap.
    listFn.mockImplementation(((options: { query: { page?: number } }) => {
      const page = options.query.page ?? 1;
      return Promise.resolve({
        data: {
          count: 9999,
          next: `http://test/?page=${page + 1}`,
          previous: null,
          results: [
            makeSnapshot({
              id: page,
              snapshot_date: "2026-05-01",
              market_value: "100.00",
            }),
          ],
        },
      });
    }) as unknown as typeof portfolioSnapshotsList);

    renderDetail();

    expect(
      await screen.findByText(/older history was truncated/i),
    ).toBeInTheDocument();
  });

  it("shows an empty value-history state when there are no snapshots", async () => {
    stubSinglePage([]);
    renderDetail();

    expect(await screen.findByText("Yubel Deck")).toBeInTheDocument();
    expect(await screen.findByText(/No value history/i)).toBeInTheDocument();
    expect(screen.queryByTestId("value-chart")).not.toBeInTheDocument();
  });

  it("shows an error card with retry when the portfolio fails to load", async () => {
    retrieveOptions.mockImplementation(
      () =>
        ({
          queryKey: [{ _id: "portfolioPortfoliosRetrieve" }],
          queryFn: async () => {
            throw new Error("403");
          },
        }) as unknown as ReturnType<typeof portfolioPortfoliosRetrieveOptions>,
    );
    renderDetail();

    expect(
      await screen.findByText(/Couldn.t load this portfolio/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows a dedicated error when the value history fails to load", async () => {
    // Portfolio loads; the history page-walk rejects.
    listFn.mockImplementation((() =>
      Promise.reject(new Error("500"))) as unknown as typeof portfolioSnapshotsList);

    renderDetail();

    expect(
      await screen.findByText(/Couldn.t load value history/i),
    ).toBeInTheDocument();
    // The portfolio header still rendered, only the history errored.
    expect(screen.getByText("Yubel Deck")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
