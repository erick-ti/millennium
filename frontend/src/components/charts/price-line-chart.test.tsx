import React from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// ResponsiveContainer measures its parent via ResizeObserver/getBoundingClientRect,
// which report 0 in jsdom — recharts then renders nothing. Replace it with a
// wrapper that clones the chart child with explicit dimensions (the standard
// recharts-under-jsdom approach). Runtime-only mock; types still resolve.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement }) =>
      React.cloneElement(
        children as React.ReactElement<{ width?: number; height?: number }>,
        { width: 800, height: 300 },
      ),
  };
});

import { PriceLineChart } from "./price-line-chart";

describe("PriceLineChart", () => {
  const data = [
    { date: "2026-05-10", price: 10 },
    { date: "2026-05-11", price: 12.5 },
    { date: "2026-05-12", price: 11 },
  ];

  it("renders an SVG line chart for the given points", () => {
    const { container } = render(<PriceLineChart data={data} />);
    expect(container.querySelector("svg")).toBeTruthy();
    // recharts draws the series as a <g class="recharts-line"> with a curve path.
    expect(container.querySelector(".recharts-line")).toBeTruthy();
  });

  it("exposes every point as accessible text (date + formatted price)", () => {
    // The sr-only <table> is the chart's text alternative (review C3) AND a
    // robust point-count assertion: the SVG dot count is unreliable under jsdom
    // and dot={false} anyway. One <tbody> row per data point, in order.
    render(<PriceLineChart data={data} label="Unlimited price" />);
    const table = screen.getByRole("table", { name: "Unlimited price" });
    const bodyRows = within(table)
      .getAllByRole("row")
      // drop the header row
      .filter((row) => within(row).queryAllByRole("columnheader").length === 0);
    expect(bodyRows).toHaveLength(3);
    expect(within(bodyRows[0]).getByText("2026-05-10")).toBeInTheDocument();
    expect(within(bodyRows[0]).getByText("$10.00")).toBeInTheDocument();
    expect(within(bodyRows[1]).getByText("$12.50")).toBeInTheDocument();
  });

  it("gives the chart an accessible name from the label", () => {
    render(<PriceLineChart data={data} label="Unlimited price, May 10 to May 12, 3 points" />);
    expect(
      screen.getByRole("img", { name: "Unlimited price, May 10 to May 12, 3 points" }),
    ).toBeInTheDocument();
  });

  it("renders a single-point series without crashing", () => {
    render(<PriceLineChart data={[{ date: "2026-05-10", price: 5 }]} />);
    const table = screen.getByRole("table");
    expect(within(table).getByText("$5.00")).toBeInTheDocument();
  });

  it("renders without crashing for an empty series", () => {
    const { container } = render(<PriceLineChart data={[]} />);
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("labels the value column 'Market price' by default", () => {
    render(<PriceLineChart data={data} />);
    expect(
      screen.getByRole("columnheader", { name: "Market price" }),
    ).toBeInTheDocument();
  });

  it("uses a custom seriesLabel for the value column", () => {
    // Slice 5 reuses this pure chart for a portfolio value series; the series
    // name must change from the card-price default, not silently mislabel.
    render(<PriceLineChart data={data} seriesLabel="Portfolio value" />);
    expect(
      screen.getByRole("columnheader", { name: "Portfolio value" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Market price" }),
    ).not.toBeInTheDocument();
  });

  it("marks partial-coverage points in the accessible table when points carry coverage", () => {
    // A coverage-carrying (aggregate) series exposes a Coverage column so a
    // coverage-driven dip isn't read as a real value move (Codex adversarial
    // review, 2026-05-29).
    render(
      <PriceLineChart
        seriesLabel="Portfolio value"
        data={[
          { date: "2026-05-10", price: 10, complete: true },
          { date: "2026-05-11", price: 6, complete: false },
        ]}
      />,
    );
    expect(
      screen.getByRole("columnheader", { name: "Coverage" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Partial coverage")).toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
  });

  it("omits the coverage column for a coverage-agnostic series (card prices)", () => {
    // `data` carries no `complete` flag → the card price chart keeps its
    // original 2-column table, unchanged.
    render(<PriceLineChart data={data} />);
    expect(
      screen.queryByRole("columnheader", { name: "Coverage" }),
    ).not.toBeInTheDocument();
  });
});
