import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PortfolioValueSnapshot } from "@/lib/api";

import { PortfolioMetrics } from "./portfolio-metrics";

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

describe("PortfolioMetrics", () => {
  it("shows a 'not yet valued' notice (no $0 rows) when there is no snapshot", () => {
    render(<PortfolioMetrics snapshot={null} />);
    expect(screen.getByText(/not yet valued/i)).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("renders all money fields and a positive gain when coverage is complete", () => {
    render(<PortfolioMetrics snapshot={makeSnapshot()} />);
    expect(screen.getByText("$1,240.50")).toBeInTheDocument();
    expect(screen.getByText("$1,054.40")).toBeInTheDocument();
    expect(screen.getByText("$980.00")).toBeInTheDocument();
    // Signed value + up-arrow direction marker for a gain.
    expect(screen.getByText(/\+\$260\.50\s*▲/)).toBeInTheDocument();
    expect(screen.getByText(/15\/15 priced/)).toBeInTheDocument();
  });

  it("renders a negative gain (a real loss) when complete", () => {
    render(<PortfolioMetrics snapshot={makeSnapshot({ unrealized_gain: "-30.00" })} />);
    // Signed value + down-arrow direction marker for a loss.
    expect(screen.getByText(/-\$30\.00\s*▼/)).toBeInTheDocument();
  });

  it("shows 'partial coverage' for a NULL gain instead of $0", () => {
    render(
      <PortfolioMetrics
        snapshot={makeSnapshot({
          unrealized_gain: null,
          is_complete: false,
          market_value_complete: false,
          cost_basis_complete: false,
          priced_card_count: 9,
          costed_card_count: 14,
        })}
      />,
    );
    expect(screen.getByText(/partial coverage/i)).toBeInTheDocument();
    expect(screen.getByText(/9\/15 priced · 14\/15 costed/)).toBeInTheDocument();
  });

  it("shows 'No cards' coverage for an empty portfolio", () => {
    render(
      <PortfolioMetrics
        snapshot={makeSnapshot({
          total_card_count: 0,
          priced_card_count: 0,
          costed_card_count: 0,
          market_value: "0.00",
          liquidation_value: "0.00",
          cost_basis: "0.00",
          unrealized_gain: "0.00",
        })}
      />,
    );
    expect(screen.getByText(/no cards/i)).toBeInTheDocument();
  });
});
