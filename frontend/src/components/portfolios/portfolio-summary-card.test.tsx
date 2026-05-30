import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Portfolio, PortfolioValueSnapshot } from "@/lib/api";

import { PortfolioSummaryCard } from "./portfolio-summary-card";

// next/link needs the App Router context; stub it to a plain anchor so href
// assertions are deterministic without mounting a router.
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

function makeSnapshot(
  overrides: Partial<PortfolioValueSnapshot> = {},
): PortfolioValueSnapshot {
  return {
    id: 1,
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

describe("PortfolioSummaryCard", () => {
  it("links the name to the drill-in route and shows the latest value", () => {
    const portfolio: Portfolio = {
      id: 3,
      name: "Yubel Deck",
      latest_snapshot: makeSnapshot(),
    };
    render(<PortfolioSummaryCard portfolio={portfolio} />);

    expect(screen.getByRole("link", { name: "Yubel Deck" })).toHaveAttribute(
      "href",
      "/portfolios/3",
    );
    expect(screen.getByText("$1,240.50")).toBeInTheDocument();
  });

  it("renders a NULL-safe 'not yet valued' card when never valued", () => {
    const portfolio: Portfolio = {
      id: 4,
      name: "Fresh Import",
      latest_snapshot: null,
    };
    render(<PortfolioSummaryCard portfolio={portfolio} />);

    expect(screen.getByRole("link", { name: "Fresh Import" })).toHaveAttribute(
      "href",
      "/portfolios/4",
    );
    expect(screen.getByText(/not yet valued/i)).toBeInTheDocument();
  });
});
