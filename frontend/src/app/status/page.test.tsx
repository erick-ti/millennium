import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ChecksStatus,
  PipelineStage,
  RecentRun,
  StatusOverview,
} from "@/lib/api";
import {
  statusChecksRetrieveOptions,
  statusOverviewRetrieveOptions,
} from "@/lib/api";

import StatusPage from "./page";

vi.mock("@/lib/api", () => ({
  statusOverviewRetrieveOptions: vi.fn(),
  statusChecksRetrieveOptions: vi.fn(),
}));

const overviewOptions = vi.mocked(statusOverviewRetrieveOptions);
const checksOptions = vi.mocked(statusChecksRetrieveOptions);

function makeStage(overrides: Partial<PipelineStage> = {}): PipelineStage {
  return {
    key: "metadata",
    label: "Metadata sync",
    scheduled_utc: "02:00",
    status: "green",
    last_run_at: "2026-06-16T02:02:00Z",
    green_today: true,
    metric_label: "cards",
    metric_value: 14400,
    depends_on: null,
    ...overrides,
  };
}

const RECENT: RecentRun[] = [
  {
    kind: "tcgcsv_pricing",
    status: "success",
    created_at: "2026-06-16T03:08:00Z",
    card_count: null,
    printing_count: null,
    product_count: 45607,
    price_row_count: 59236,
  },
  {
    kind: "ygoprodeck_metadata",
    status: "success",
    created_at: "2026-06-16T02:02:00Z",
    card_count: 14400,
    printing_count: 43313,
    product_count: null,
    price_row_count: null,
  },
];

function makeOverview(overrides: Partial<StatusOverview> = {}): StatusOverview {
  return {
    app: {
      version: "abc1234",
      environment: "prod",
      server_time: "2026-06-16T23:45:00Z",
      uptime_seconds: 19000,
    },
    pipeline: [
      makeStage({ key: "metadata", label: "Metadata sync", scheduled_utc: "02:00" }),
      makeStage({
        key: "pricing",
        label: "Pricing sync",
        scheduled_utc: "03:00",
        metric_label: "prices",
        metric_value: 59236,
      }),
      makeStage({
        key: "valuation",
        label: "Valuation",
        scheduled_utc: "04:00",
        metric_label: "holdings valued",
        metric_value: 12,
        depends_on: "pricing",
      }),
      makeStage({
        key: "alerts",
        label: "Alerts",
        scheduled_utc: "05:00",
        metric_label: "events",
        metric_value: 0,
        depends_on: "pricing",
      }),
    ],
    catalog: {
      cards: 14400,
      printings: 43313,
      price_snapshots: 59236,
      portfolios: 3,
      owned_holdings: 42,
      owned_copies: 120,
    },
    valuation: {
      as_of: "2026-06-16",
      market_value: "1234.50",
      complete: true,
      portfolios_valued: 3,
    },
    recent_runs: RECENT,
    ...overrides,
  };
}

function stubOverview(impl: () => StatusOverview) {
  overviewOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "statusOverviewRetrieve" }],
        queryFn: async () => impl(),
      }) as unknown as ReturnType<typeof statusOverviewRetrieveOptions>,
  );
}

function notConfiguredChecks(): ChecksStatus {
  return { configured: false, available: false, error: null, backup: null, cd: null };
}

function stubChecks(impl: () => ChecksStatus) {
  checksOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "statusChecksRetrieve" }],
        queryFn: async () => impl(),
      }) as unknown as ReturnType<typeof statusChecksRetrieveOptions>,
  );
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <StatusPage />
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}

function greyStage(key: string): PipelineStage {
  return makeStage({
    key,
    label: key,
    status: "grey",
    green_today: false,
    last_run_at: null,
    metric_value: null,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  // Default: the Healthchecks tier is unconfigured, so the backup/CD flow nodes render
  // grey. Tests exercising the lit-up / unavailable paths override with stubChecks().
  stubChecks(() => notConfiguredChecks());
});

describe("StatusPage", () => {
  it("shows a loading state before data resolves", () => {
    stubOverview(() => makeOverview());
    renderPage();

    expect(screen.getByRole("status")).toHaveTextContent(/loading status/i);
  });

  it("renders the four internal pipeline stages plus the pending check stages", async () => {
    stubOverview(() => makeOverview());
    renderPage();

    expect(await screen.findByText("Metadata sync")).toBeInTheDocument();
    expect(screen.getByText("Pricing sync")).toBeInTheDocument();
    expect(screen.getByText("Valuation")).toBeInTheDocument();
    expect(screen.getByText("Alerts")).toBeInTheDocument();
    // The flow shows the COMPLETE nightly chain; backup + CD render grey "not
    // configured" by default (no Healthchecks key in this stub).
    expect(screen.getByText("Database backup")).toBeInTheDocument();
    expect(screen.getByText("Continuous deploy")).toBeInTheDocument();
    expect(screen.getAllByText(/not configured/i)).toHaveLength(2);
  });

  it("summarizes an all-green day and labels each stage OK", async () => {
    stubOverview(() => makeOverview());
    renderPage();

    expect(await screen.findByText("All green today")).toBeInTheDocument();
    expect(screen.getAllByText("OK")).toHaveLength(4);
  });

  it("draws the pricing dependency on valuation and alerts", async () => {
    stubOverview(() => makeOverview());
    renderPage();

    await screen.findByText("Valuation");
    expect(screen.getAllByText(/gated on pricing/i)).toHaveLength(2);
  });

  it("surfaces a failed stage in the summary and its status label", async () => {
    stubOverview(() =>
      makeOverview({
        pipeline: [
          makeStage({ key: "pricing", label: "Pricing sync", status: "red", green_today: false }),
        ],
      }),
    );
    renderPage();

    expect(await screen.findByText("1 stage failed")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("renders catalog cardinality", async () => {
    stubOverview(() => makeOverview());
    renderPage();

    await screen.findByText("Owned holdings");
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
  });

  it("renders the latest portfolio value", async () => {
    stubOverview(() => makeOverview());
    renderPage();

    expect(await screen.findByText("$1,234.50")).toBeInTheDocument();
  });

  it("is NULL-safe when nothing has been valued yet", async () => {
    stubOverview(() =>
      makeOverview({
        valuation: { as_of: null, market_value: null, complete: null, portfolios_valued: 0 },
      }),
    );
    renderPage();

    expect(await screen.findByText("Not yet valued.")).toBeInTheDocument();
  });

  it("flags partial valuation coverage", async () => {
    stubOverview(() =>
      makeOverview({
        valuation: {
          as_of: "2026-06-16",
          market_value: "10.00",
          complete: false,
          portfolios_valued: 1,
        },
      }),
    );
    renderPage();

    expect(await screen.findByText(/partial coverage/i)).toBeInTheDocument();
  });

  it("renders the deployed version, environment and uptime", async () => {
    stubOverview(() => makeOverview());
    renderPage();

    expect(await screen.findByText("abc1234")).toBeInTheDocument();
    expect(screen.getByText("prod")).toBeInTheDocument();
    expect(screen.getByText("5h 16m")).toBeInTheDocument();
  });

  it("lists recent sync runs newest first", async () => {
    stubOverview(() => makeOverview());
    renderPage();

    expect(await screen.findByText("Recent sync runs")).toBeInTheDocument();
    expect(screen.getByText("Metadata")).toBeInTheDocument();
    expect(screen.getByText("Pricing")).toBeInTheDocument();
  });

  it("shows a first-load error with retry", async () => {
    stubOverview(() => {
      throw new Error("500");
    });
    renderPage();

    expect(await screen.findByText(/couldn.t load status/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("does NOT green-wash an all-grey (never-run) pipeline", async () => {
    stubOverview(() =>
      makeOverview({
        pipeline: ["metadata", "pricing", "valuation", "alerts"].map(greyStage),
      }),
    );
    renderPage();

    expect(await screen.findByText("Awaiting first run")).toBeInTheDocument();
    expect(screen.queryByText("All green today")).not.toBeInTheDocument();
    // Every stage shows its true "No data" state below the (now honest) summary.
    expect(screen.getAllByText("No data")).toHaveLength(4);
  });

  it("summarizes attention and labels amber/grey stage states", async () => {
    stubOverview(() =>
      makeOverview({
        pipeline: [
          makeStage({ key: "metadata", label: "Metadata sync", status: "amber", green_today: false }),
          greyStage("pricing"),
        ],
      }),
    );
    renderPage();

    expect(await screen.findByText("1 stage needs attention")).toBeInTheDocument();
    expect(screen.getByText("Attention")).toBeInTheDocument();
    expect(screen.getByText("No data")).toBeInTheDocument();
  });

  it("keeps the last-good dashboard when a LATER poll fails (only first-load errors)", async () => {
    let calls = 0;
    stubOverview(() => {
      calls += 1;
      if (calls >= 2) throw new Error("poll failed");
      return makeOverview();
    });
    const { queryClient } = renderPage();

    // First fetch succeeded → the dashboard rendered.
    await screen.findByText("Owned holdings");

    // A subsequent poll throws; query.data survives a failed refetch, so the dashboard
    // must stay mounted and the indicator flips to disconnected (NOT the error panel).
    await queryClient.refetchQueries();

    expect(await screen.findByText(/disconnected — showing last good/i)).toBeInTheDocument();
    expect(screen.getByText("Owned holdings")).toBeInTheDocument();
    expect(screen.queryByText(/couldn.t load status/i)).not.toBeInTheDocument();
  });

  it("lights the backup + CD flow nodes from Healthchecks", async () => {
    stubOverview(() => makeOverview());
    stubChecks(() => ({
      configured: true,
      available: true,
      error: null,
      backup: {
        name: "Backup",
        status: "up",
        last_ping_at: "2026-06-16T06:00:03Z",
        n_pings: 30,
      },
      cd: {
        name: "Deploy",
        status: "grace",
        last_ping_at: "2026-06-16T23:40:00Z",
        n_pings: 700,
      },
    }));
    renderPage();

    expect(await screen.findByText("Up")).toBeInTheDocument(); // backup is up
    expect(screen.getByText("Late")).toBeInTheDocument(); // cd is in grace
    expect(screen.queryByText(/not configured/i)).not.toBeInTheDocument();
  });

  it("flags an unavailable Healthchecks tier in the headline and the nodes", async () => {
    stubOverview(() => makeOverview()); // 4 green syncs
    stubChecks(() => ({
      configured: true,
      available: false,
      error: "ConnectError",
      backup: null,
      cd: null,
    }));
    renderPage();

    // A configured-but-unavailable tier can't confirm backups → headline isn't green.
    expect(await screen.findByText("Checks unavailable")).toBeInTheDocument();
    expect(screen.queryByText("All green today")).not.toBeInTheDocument();
    // ...and both nodes carry the node-level reason (distinct from the headline).
    expect(screen.getAllByText(/healthchecks.*unavailable/i)).toHaveLength(2);
  });

  it("never shows 'All green today' while a check is down", async () => {
    stubOverview(() => makeOverview()); // 4 green syncs
    stubChecks(() => ({
      configured: true,
      available: true,
      error: null,
      backup: {
        name: "Backup",
        status: "down",
        last_ping_at: "2026-06-16T06:00:03Z",
        n_pings: 30,
      },
      cd: null,
    }));
    renderPage();

    expect(await screen.findByText("1 check down")).toBeInTheDocument();
    expect(screen.queryByText("All green today")).not.toBeInTheDocument();
    expect(screen.getByText("Down")).toBeInTheDocument(); // down → red "Down"
  });

  it("treats paused/new checks as neutral in the headline and tolerates a null last-ping", async () => {
    stubOverview(() => makeOverview());
    stubChecks(() => ({
      configured: true,
      available: true,
      error: null,
      backup: { name: "B", status: "paused", last_ping_at: null, n_pings: 5 },
      cd: { name: "D", status: "new", last_ping_at: null, n_pings: 0 },
    }));
    renderPage();

    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(screen.getByText("No pings yet")).toBeInTheDocument(); // new → grey
    expect(screen.queryByText(/last ping/i)).not.toBeInTheDocument(); // null last-ping
    // paused (intentional) + new (never-pinged) are neutral — the headline stays green.
    expect(screen.getByText("All green today")).toBeInTheDocument();
  });

  it("flags an overdue (grace) check in the headline", async () => {
    stubOverview(() => makeOverview()); // 4 green syncs
    stubChecks(() => ({
      configured: true,
      available: true,
      error: null,
      backup: {
        name: "B",
        status: "grace",
        last_ping_at: "2026-06-16T06:00:03Z",
        n_pings: 30,
      },
      cd: null,
    }));
    renderPage();

    expect(await screen.findByText("1 check overdue")).toBeInTheDocument();
    expect(screen.queryByText("All green today")).not.toBeInTheDocument();
    expect(screen.getByText("Late")).toBeInTheDocument(); // grace → amber "Late" node
  });

  it("shows 'check not found' when configured but the slug is unmatched", async () => {
    stubOverview(() => makeOverview());
    stubChecks(() => ({
      configured: true,
      available: true,
      error: null,
      backup: null,
      cd: null,
    }));
    renderPage();

    expect(await screen.findAllByText(/check not found/i)).toHaveLength(2);
  });

  it("isolates a checks-query failure — dashboard renders, nodes show 'couldn't load'", async () => {
    stubOverview(() => makeOverview());
    stubChecks(() => {
      throw new Error("checks 500");
    });
    renderPage();

    // The overview still renders the dashboard...
    expect(await screen.findByText("Owned holdings")).toBeInTheDocument();
    expect(screen.getByText("Metadata sync")).toBeInTheDocument();
    // ...the backup/CD nodes show the couldn't-load note (not a permanent "loading…")...
    expect(screen.getAllByText(/couldn.t load/i)).toHaveLength(2);
    // ...the headline reflects that external health is unconfirmable (not green)...
    expect(screen.getByText("Checks unavailable")).toBeInTheDocument();
    expect(screen.queryByText("All green today")).not.toBeInTheDocument();
    // ...and the overview error panel is NOT surfaced.
    expect(screen.queryByText(/couldn.t load status/i)).not.toBeInTheDocument();
  });
});
