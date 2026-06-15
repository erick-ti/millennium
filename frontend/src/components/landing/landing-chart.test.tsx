import React from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// ResponsiveContainer reports 0 size in jsdom; clone the chart child with fixed
// dimensions (the project's standard recharts-under-jsdom approach, mirroring
// price-line-chart.test.tsx). The sr-only data table renders independently of
// this, but the mock keeps recharts quiet.
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

import { CURVE } from "./data";
import { LandingChart } from "./landing-chart";

describe("LandingChart", () => {
  it("mirrors every CURVE point in the sr-only data table with coverage labels", () => {
    // The accessibility data mirror is an advertised feature (the SPEC manifest
    // lists "sr-only data tables mirror every chart"); guard that the screen-
    // reader table reflects every snapshot and labels coverage correctly.
    render(<LandingChart />);
    const table = screen.getByRole("table");
    const bodyRows = within(table)
      .getAllByRole("row")
      .filter((row) => within(row).queryAllByRole("columnheader").length === 0);
    expect(bodyRows).toHaveLength(CURVE.length);

    const partialCount = CURVE.filter((point) => !point.complete).length;
    expect(screen.getAllByText("Partial")).toHaveLength(partialCount);
    expect(screen.getAllByText("Complete")).toHaveLength(CURVE.length - partialCount);
  });

  it("exposes the chart with an accessible name", () => {
    render(<LandingChart />);
    expect(
      screen.getByRole("img", { name: "Portfolio value over time" }),
    ).toBeInTheDocument();
  });
});
