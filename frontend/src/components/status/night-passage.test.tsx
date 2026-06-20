import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ChecksStatus, PipelineStage } from "@/lib/api";

import { NightPassage } from "./night-passage";

function stage(
  key: string,
  label: string,
  scheduled_utc: string,
  status: string,
  metric_label: string,
  metric_value: number | null,
  depends_on: string | null,
): PipelineStage {
  return {
    key,
    label,
    scheduled_utc,
    status,
    last_run_at: status === "grey" ? null : `2026-06-19T${scheduled_utc}:00Z`,
    green_today: status === "green",
    metric_label,
    metric_value,
    depends_on,
  };
}

const GREEN: PipelineStage[] = [
  stage("metadata", "Metadata sync", "02:00", "green", "cards", 14417, null),
  stage("pricing", "Pricing sync", "03:00", "green", "prices", 59223, null),
  stage("valuation", "Valuation", "04:00", "green", "holdings valued", 12, "pricing"),
  stage("alerts", "Alerts", "05:00", "green", "events", 0, "pricing"),
];
const FAILED: PipelineStage[] = [
  stage("metadata", "Metadata sync", "02:00", "green", "cards", 14417, null),
  stage("pricing", "Pricing sync", "03:00", "red", "prices", null, null),
  stage("valuation", "Valuation", "04:00", "amber", "holdings valued", null, "pricing"),
  stage("alerts", "Alerts", "05:00", "amber", "events", null, "pricing"),
];
const CHECKS: ChecksStatus = {
  configured: true,
  available: true,
  error: null,
  backup: { name: "Backup", status: "up", last_ping_at: "2026-06-19T06:00:03Z", n_pings: 30 },
  cd: { name: "Deploy", status: "up", last_ping_at: "2026-06-19T11:58:00Z", n_pings: 700 },
};

describe("NightPassage", () => {
  it("renders an accessible gate button per stage and check, in chronological order", () => {
    render(
      <NightPassage
        stages={GREEN}
        checks={CHECKS}
        deployedSha="0e693b0"
        serverTime="2026-06-19T12:00:00Z"
        severity="green"
      />,
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(6);
    const names = buttons.map((b) => b.getAttribute("aria-label") ?? "");
    expect(names[0]).toMatch(/Metadata sync/);
    expect(names[1]).toMatch(/Pricing sync/);
    expect(names[5]).toMatch(/Continuous deploy/);
  });

  it("carries the deployed SHA on the continuous-deploy gate", () => {
    render(
      <NightPassage
        stages={GREEN}
        checks={CHECKS}
        deployedSha="0e693b0"
        serverTime="2026-06-19T12:00:00Z"
        severity="green"
      />,
    );
    expect(
      screen.getByRole("button", { name: /continuous deploy/i }),
    ).toHaveAccessibleName(/deployed 0e693b0/);
  });

  it("states the pricing dependency in the gate label", () => {
    render(<NightPassage stages={GREEN} checks={CHECKS} severity="green" />);
    expect(screen.getByRole("button", { name: /valuation/i })).toHaveAccessibleName(
      /gated on pricing/i,
    );
  });

  it("expands a gate to its run detail + gate explanation on click", async () => {
    const user = userEvent.setup();
    render(<NightPassage stages={GREEN} checks={CHECKS} severity="green" />);

    const valuation = screen.getByRole("button", { name: /valuation/i });
    expect(valuation).toHaveAttribute("aria-expanded", "false");

    await user.click(valuation);
    expect(valuation).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/freshness gate/i)).toBeInTheDocument();
    expect(screen.getByText(/gated on pricing/i)).toBeInTheDocument();
  });

  it("escalates the dependency caption when pricing failed", async () => {
    const user = userEvent.setup();
    render(<NightPassage stages={FAILED} checks={CHECKS} severity="loss" />);

    await user.click(screen.getByRole("button", { name: /valuation/i }));
    expect(screen.getByText(/pricing failed — gate unmet/i)).toBeInTheDocument();
  });

  it("captions the barque with the live server time", () => {
    render(<NightPassage stages={GREEN} checks={CHECKS} severity="green" serverTime="2026-06-19T03:42:00Z" />);
    expect(screen.getByText(/now .*UTC/i)).toBeInTheDocument();
    expect(screen.getByText(/between pricing and valuation/i)).toBeInTheDocument();
  });

  it("does NOT claim the run is 'complete' past dawn when the rollup failed", () => {
    render(<NightPassage stages={FAILED} checks={CHECKS} severity="loss" serverTime="2026-06-19T12:00:00Z" />);
    expect(screen.getByText(/did not finish clean/i)).toBeInTheDocument();
    expect(screen.queryByText(/run is complete/i)).not.toBeInTheDocument();
  });

  it("claims 'complete' past dawn only when all-green", () => {
    render(<NightPassage stages={GREEN} checks={CHECKS} severity="green" serverTime="2026-06-19T12:00:00Z" />);
    expect(screen.getByText(/run is complete — past dawn/i)).toBeInTheDocument();
  });
});
