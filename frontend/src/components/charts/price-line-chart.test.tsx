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
});
