import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  PaginatedPortfolioList,
  Portfolio,
  PortfolioValueSnapshot,
} from "@/lib/api";
import { portfolioPortfoliosListOptions } from "@/lib/api";

import PortfoliosPage from "./page";

// next/link → plain anchor so href assertions don't need a router.
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

// Only the read-option factory the page calls is stubbed; types still resolve
// from the real module (vi.mock is runtime-only).
vi.mock("@/lib/api", () => ({
  portfolioPortfoliosListOptions: vi.fn(),
}));

const listOptions = vi.mocked(portfolioPortfoliosListOptions);

function makeSnapshot(
  overrides: Partial<PortfolioValueSnapshot> = {},
): PortfolioValueSnapshot {
  return {
    id: 1,
    portfolio: 1,
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

function makePortfolio(overrides: Partial<Portfolio> = {}): Portfolio {
  return {
    id: 1,
    name: "Yubel Deck",
    latest_snapshot: makeSnapshot(),
    ...overrides,
  };
}

function stub(impl: (page: number) => PaginatedPortfolioList) {
  listOptions.mockImplementation((options) => {
    const page = options?.query?.page ?? 1;
    return {
      queryKey: [{ _id: "portfolioPortfoliosList", query: { page } }],
      queryFn: async () => impl(page),
    } as unknown as ReturnType<typeof portfolioPortfoliosListOptions>;
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PortfoliosPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PortfoliosPage", () => {
  it("shows a loading skeleton before data resolves", () => {
    stub(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(
      screen.getByRole("status", { name: /loading portfolios/i }),
    ).toBeInTheDocument();
  });

  it("renders one card per portfolio with a name link and NULL-safe gain", async () => {
    stub(() => ({
      count: 2,
      next: null,
      previous: null,
      results: [
        makePortfolio({
          id: 3,
          name: "Yubel Deck",
          latest_snapshot: makeSnapshot({
            market_value: "1240.50",
            unrealized_gain: "260.50",
          }),
        }),
        makePortfolio({
          id: 4,
          name: "Blue-Eyes",
          latest_snapshot: makeSnapshot({
            market_value: "842.00",
            unrealized_gain: null,
            is_complete: false,
            market_value_complete: false,
            cost_basis_complete: false,
            total_card_count: 12,
            priced_card_count: 9,
            costed_card_count: 12,
          }),
        }),
      ],
    }));
    renderPage();

    const yubel = await screen.findByRole("link", { name: "Yubel Deck" });
    expect(yubel).toHaveAttribute("href", "/portfolios/3");
    expect(screen.getByRole("link", { name: "Blue-Eyes" })).toHaveAttribute(
      "href",
      "/portfolios/4",
    );
    expect(screen.getByText("$1,240.50")).toBeInTheDocument();
    expect(screen.getByText(/\+\$260\.50/)).toBeInTheDocument();
    // Blue-Eyes has partial coverage → gain shown as partial, never $0.
    expect(screen.getByText(/partial coverage/i)).toBeInTheDocument();
    expect(screen.getByText(/2 portfolios/)).toBeInTheDocument();
  });

  it("renders a friendly empty state and no pagination footer", async () => {
    stub(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(await screen.findByText(/No portfolios yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();
  });

  it("renders a first-load error with retry and no stranding back-control", async () => {
    stub(() => {
      throw new Error("403");
    });
    renderPage();

    expect(
      await screen.findByText(/Couldn.t load your portfolios/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /back to page/i }),
    ).not.toBeInTheDocument();
  });

  it("pages forward, requesting the next page and flipping boundary states", async () => {
    stub((page) =>
      page === 1
        ? {
            count: 150,
            next: "http://test/?page=2",
            previous: null,
            results: [makePortfolio({ id: 1, name: "Portfolio One" })],
          }
        : {
            count: 150,
            next: null,
            previous: "http://test/?page=1",
            results: [makePortfolio({ id: 2, name: "Portfolio Two" })],
          },
    );
    const user = userEvent.setup();
    renderPage();

    expect(
      await screen.findByRole("link", { name: "Portfolio One" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /prev/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /next/i }));

    expect(
      await screen.findByRole("link", { name: "Portfolio Two" }),
    ).toBeInTheDocument();
    expect(listOptions).toHaveBeenCalledWith({ query: { page: 2 } });
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /prev/i })).toBeEnabled();
  });
});
