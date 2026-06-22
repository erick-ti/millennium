import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AlertEvent,
  AlertRule,
  PaginatedAlertEventList,
} from "@/lib/api";
import {
  alertsEventsListOptions,
  alertsRulesCreate,
  alertsRulesListOptions,
  csrfRetrieve,
} from "@/lib/api";

import AlertsPage from "./page";

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

vi.mock("@/lib/api", () => ({
  alertsEventsListOptions: vi.fn(),
  alertsRulesListOptions: vi.fn(),
  alertsRulesCreate: vi.fn(),
  alertsEventsListQueryKey: vi.fn(() => [{ _id: "alertsEventsList" }]),
  alertsRulesListQueryKey: vi.fn(() => [{ _id: "alertsRulesList" }]),
  // seedCsrf (via lib/csrf) calls this on a write 403; resolve so the fire-and-forget
  // re-seed never throws (the import-batch-detail.test.tsx convention).
  csrfRetrieve: vi.fn(async () => ({})),
}));

// Auth state is controllable so the create form (owner) vs the read-only notice (demo)
// can both be exercised. Owner by default; a demo test flips `auth.canWrite`.
const auth = vi.hoisted(() => ({ canWrite: true }));
vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: { id: 1, username: auth.canWrite ? "reader" : "demo", email: "" },
    isLoading: false,
    isAuthenticated: true,
    isDemo: !auth.canWrite,
    canWrite: auth.canWrite,
    refetch: vi.fn(),
  }),
}));

const eventsOptions = vi.mocked(alertsEventsListOptions);
const rulesOptions = vi.mocked(alertsRulesListOptions);
const createRuleFn = vi.mocked(alertsRulesCreate);
const csrfMock = vi.mocked(csrfRetrieve);

function makeEvent(overrides: Partial<AlertEvent> = {}): AlertEvent {
  return {
    id: 1,
    rule: 1,
    rule_name: "Big up moves",
    rule_threshold_pct: "10.00",
    rule_window_days: 30,
    rule_direction: "up",
    printing: 1,
    card_id: 5,
    card_name: "Ash Blossom & Joyous Spring",
    set_code: "L5DD-ENC09",
    set_rarity: "Common",
    variant_label: null,
    edition: "first",
    triggered_on: "2026-05-31",
    start_price: "10.00",
    end_price: "12.00",
    pct_change: "20.00",
    dollar_change: "2.00",
    created_at: "2026-05-31T00:00:00Z",
    ...overrides,
  };
}

function makeRule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: 1,
    name: "Big up moves",
    threshold_pct: "10.00",
    window_days: 30,
    direction: "up",
    is_active: true,
    created_at: "2026-05-31T00:00:00Z",
    updated_at: "2026-05-31T00:00:00Z",
    ...overrides,
  };
}

function stubEvents(impl: (page: number) => PaginatedAlertEventList) {
  eventsOptions.mockImplementation((options) => {
    const page = options?.query?.page ?? 1;
    return {
      queryKey: [{ _id: "alertsEventsList", query: options?.query }],
      queryFn: async () => impl(page),
    } as unknown as ReturnType<typeof alertsEventsListOptions>;
  });
}

function stubRules(rules: AlertRule[]) {
  rulesOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "alertsRulesList" }],
        queryFn: async () => ({
          count: rules.length,
          next: null,
          previous: null,
          results: rules,
        }),
      }) as unknown as ReturnType<typeof alertsRulesListOptions>,
  );
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AlertsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  auth.canWrite = true;
  stubEvents(() => ({ count: 0, next: null, previous: null, results: [] }));
  stubRules([]);
  // Default: create succeeds (overridden per-test for the error path).
  createRuleFn.mockResolvedValue({
    data: makeRule(),
    error: undefined,
    response: { status: 201 } as Response,
    request: {} as Request,
  });
});

describe("AlertsPage — feed", () => {
  it("shows a loading skeleton before data resolves", () => {
    renderPage();
    expect(
      screen.getByRole("status", { name: /loading alerts/i }),
    ).toBeInTheDocument();
  });

  it("queries the feed with page 1 and no rule filter on first load", () => {
    renderPage();
    expect(eventsOptions).toHaveBeenCalledWith({
      query: { page: 1, rule: undefined },
    });
  });

  it("renders a feed row with the rule snapshot, card link, % and $ move", async () => {
    stubEvents(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeEvent({ card_id: 5 })],
    }));
    renderPage();

    const link = await screen.findByRole("link", {
      name: "Ash Blossom & Joyous Spring",
    });
    expect(link).toHaveAttribute("href", "/cards/5");
    expect(screen.getByText("Big up moves")).toBeInTheDocument();
    expect(screen.getByText(/≥10\.00% · 30d · up/)).toBeInTheDocument();
    expect(screen.getByText("1st Edition")).toBeInTheDocument();
    expect(screen.getByText("+20.0%")).toBeInTheDocument(); // human percent → ratio → +20.0%
    expect(screen.getByText("+$2.00")).toBeInTheDocument();
    expect(screen.getByText(/1 alert/)).toBeInTheDocument();
  });

  it("renders a loss with negative signed deltas", async () => {
    stubEvents(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeEvent({ pct_change: "-15.00", dollar_change: "-3.00" })],
    }));
    renderPage();

    expect(await screen.findByText("-15.0%")).toBeInTheDocument();
    expect(screen.getByText("-$3.00")).toBeInTheDocument();
  });

  it("renders a friendly empty state and no pagination footer", async () => {
    renderPage();
    expect(
      await screen.findByText(/No alerts have fired yet/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();
  });

  it("renders a first-load error with retry and no stranding back-control", async () => {
    stubEvents(() => {
      throw new Error("boom");
    });
    renderPage();

    expect(await screen.findByText(/Couldn.t load alerts/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /back to page/i }),
    ).not.toBeInTheDocument();
  });

  it("pages forward, preserving the rule filter (none here)", async () => {
    stubEvents((page) =>
      page === 1
        ? {
            count: 150,
            next: "http://test/?page=2",
            previous: null,
            results: [makeEvent({ id: 1, card_name: "Alert Page One" })],
          }
        : {
            count: 150,
            next: null,
            previous: "http://test/?page=1",
            results: [makeEvent({ id: 2, card_name: "Alert Page Two" })],
          },
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: "Alert Page One" });
    await user.click(screen.getByRole("button", { name: /next/i }));

    await screen.findByRole("link", { name: "Alert Page Two" });
    expect(eventsOptions).toHaveBeenCalledWith({
      query: { page: 2, rule: undefined },
    });
  });

  it("filters by rule, resetting to page 1 and sending the rule id", async () => {
    stubRules([makeRule({ id: 7, name: "Seventh rule" })]);
    stubEvents(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeEvent()],
    }));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: /Ash Blossom/ });
    await user.selectOptions(
      screen.getByRole("combobox", { name: /filter by rule/i }),
      "7",
    );

    expect(eventsOptions).toHaveBeenCalledWith({ query: { page: 1, rule: 7 } });
  });
});

describe("AlertsPage — create rule form", () => {
  it("submits the rule with the typed name and default window/direction/threshold", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: /rule name/i }), "My rule");
    await user.click(screen.getByRole("button", { name: /create rule/i }));

    expect(createRuleFn).toHaveBeenCalledWith({
      body: {
        name: "My rule",
        threshold_pct: "10",
        window_days: 30,
        direction: "any",
      },
    });
    expect(await screen.findByText(/Rule created/i)).toBeInTheDocument();
  });

  it("surfaces a 400 field error from the server", async () => {
    createRuleFn.mockResolvedValue({
      data: undefined,
      error: { threshold_pct: ["must be greater than 0"] },
      response: { status: 400 } as Response,
      request: {} as Request,
    });
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: /rule name/i }), "x");
    await user.click(screen.getByRole("button", { name: /create rule/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "must be greater than 0",
    );
  });

  it("disables submit until a name is present and the threshold is positive", async () => {
    const user = userEvent.setup();
    renderPage();

    const submit = screen.getByRole("button", { name: /create rule/i });
    expect(submit).toBeDisabled(); // name empty

    await user.type(screen.getByRole("textbox", { name: /rule name/i }), "x");
    expect(submit).toBeEnabled();

    const threshold = screen.getByRole("spinbutton", { name: /threshold %/i });
    await user.clear(threshold);
    await user.type(threshold, "0");
    expect(submit).toBeDisabled(); // non-positive threshold
  });

  it("re-seeds the CSRF cookie when create returns 403 (recoverable without reload)", async () => {
    createRuleFn.mockResolvedValue({
      data: undefined,
      // 403 with no usable detail body → the form falls back to "HTTP 403"; `{}` matches
      // hey-api's error-branch type (the success branch needs non-undefined `data`).
      error: {},
      response: { status: 403 } as Response,
      request: {} as Request,
    });
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: /rule name/i }), "x");
    await user.click(screen.getByRole("button", { name: /create rule/i }));

    // The error surfaces AND the CSRF cookie is re-seeded so the next attempt carries a token.
    expect(await screen.findByRole("alert")).toHaveTextContent(/HTTP 403/);
    expect(csrfMock).toHaveBeenCalledTimes(1);
  });

  it("hides the create form and shows a sign-in notice for the read-only demo", () => {
    auth.canWrite = false;
    renderPage();

    expect(
      screen.queryByRole("button", { name: /create rule/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /sign in/i })).toBeInTheDocument();
  });
});
