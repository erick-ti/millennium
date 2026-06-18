import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { InfraStatus } from "@/lib/api";
import { InfraTile } from "./infra-tile";

const FRESH: InfraStatus = {
  available: true,
  stale: false,
  sampled_at: "2026-06-18T04:30:00Z",
  cpu_percent: 11,
  load_1m: 0.12,
  mem_used_mb: 1411,
  mem_total_mb: 7751,
  disk_used_gb: 20.17,
  disk_total_gb: 74.78,
  net_rx_kbps: 8,
  net_tx_kbps: 1,
  cpu_series: [10, 12, 11, 13, 11],
};

describe("InfraTile", () => {
  it("renders host metrics from a fresh sample", () => {
    render(<InfraTile infra={FRESH} />);
    expect(screen.getByText("Host box")).toBeInTheDocument();
    expect(screen.getByText("11%")).toBeInTheDocument(); // CPU
    expect(screen.getByText("0.12")).toBeInTheDocument(); // load
    expect(screen.getByText(/1\.4 \/ 7\.6 GB \(18%\)/)).toBeInTheDocument(); // memory
    expect(screen.getByText(/20 \/ 75 GB \(27%\)/)).toBeInTheDocument(); // disk
    expect(screen.getByText("8 kbps / 1 kbps")).toBeInTheDocument(); // network
    // the CPU sparkline is exposed as an accessible image
    expect(
      screen.getByRole("img", { name: /CPU over the last hour/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/stale/i)).not.toBeInTheDocument();
  });

  it("shows 'awaiting' when no sample exists", () => {
    const empty: InfraStatus = {
      ...FRESH,
      available: false,
      stale: false,
      cpu_percent: null,
      cpu_series: [],
    };
    render(<InfraTile infra={empty} />);
    expect(screen.getByText("Awaiting host metrics.")).toBeInTheDocument();
    expect(screen.queryByText("11%")).not.toBeInTheDocument();
  });

  it("shows last-known values plus a stale note when the sample is old", () => {
    render(<InfraTile infra={{ ...FRESH, available: false, stale: true }} />);
    expect(screen.getByText("11%")).toBeInTheDocument(); // last-known, still shown
    expect(screen.getByText(/Stale/)).toBeInTheDocument();
  });

  it("shows 'awaiting' before the first query resolves (undefined)", () => {
    render(<InfraTile infra={undefined} />);
    expect(screen.getByText("Awaiting host metrics.")).toBeInTheDocument();
  });

  it("shows a DISTINCT 'unavailable' state on a first-load error (not green-washed 'awaiting')", () => {
    render(<InfraTile infra={undefined} error />);
    expect(screen.getByText("Host metrics unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("Awaiting host metrics.")).not.toBeInTheDocument();
  });

  it("keeps last-known values on a refetch error (data survived via keepPreviousData)", () => {
    // error=true but infra is still present → render the values, NOT the error state.
    render(<InfraTile infra={FRESH} error />);
    expect(screen.getByText("11%")).toBeInTheDocument();
    expect(screen.queryByText("Host metrics unavailable.")).not.toBeInTheDocument();
  });
});
