import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authDemoLoginCreate, csrfRetrieve } from "@/lib/api";

import { DemoCta } from "./demo-cta";

// Auth state is controllable: anonymous (the recruiter) by default; one test flips to the
// already-signed-in owner.
const h = vi.hoisted(() => ({ isAuthenticated: false, isLoading: false }));
vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: null,
    isLoading: h.isLoading,
    isAuthenticated: h.isAuthenticated,
    isDemo: false,
    canWrite: false,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  authDemoLoginCreate: vi.fn(),
  // The CTA re-seeds the CSRF cookie on a 403 before retrying; resolve so the await never throws.
  csrfRetrieve: vi.fn(async () => ({})),
}));
const demoLogin = vi.mocked(authDemoLoginCreate);
const csrfMock = vi.mocked(csrfRetrieve);

// The CTA hard-navigates via window.location.assign — stub it (jsdom's is a noop).
const assign = vi.fn();
const realLocation = window.location;

type Result = Awaited<ReturnType<typeof authDemoLoginCreate>>;
function resolved(value: { data?: unknown; response?: { status: number } }): Result {
  return value as unknown as Result;
}

beforeEach(() => {
  vi.clearAllMocks();
  h.isAuthenticated = false;
  h.isLoading = false;
  assign.mockClear();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { assign, href: "http://localhost/", pathname: "/" },
  });
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

describe("DemoCta", () => {
  it("anonymous: establishes the demo session, then hard-navigates into the app", async () => {
    demoLogin.mockResolvedValue(
      resolved({ data: { id: 1, username: "demo", email: "" }, response: { status: 200 } }),
    );
    render(<DemoCta />);

    await userEvent.click(screen.getByRole("button", { name: /enter the vault/i }));

    expect(demoLogin).toHaveBeenCalled();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/collection"));
  });

  it("anonymous + no demo seeded (no data): falls back to /login, not a read 403 bounce", async () => {
    demoLogin.mockResolvedValue(resolved({ data: undefined, response: { status: 404 } }));
    render(<DemoCta />);

    await userEvent.click(screen.getByRole("button", { name: /enter the vault/i }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
  });

  it("anonymous + CSRF 403: re-seeds and retries once, then enters the app", async () => {
    // First POST races the CSRF seed → 403; after re-seed the retry succeeds.
    demoLogin
      .mockResolvedValueOnce(resolved({ data: undefined, response: { status: 403 } }))
      .mockResolvedValueOnce(
        resolved({ data: { id: 1, username: "demo", email: "" }, response: { status: 200 } }),
      );
    render(<DemoCta />);

    await userEvent.click(screen.getByRole("button", { name: /enter the vault/i }));

    expect(csrfMock).toHaveBeenCalledTimes(1);
    expect(demoLogin).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/collection"));
  });

  it("anonymous + persistent 403: re-seeds, retries once, then falls back to /login", async () => {
    demoLogin.mockResolvedValue(resolved({ data: undefined, response: { status: 403 } }));
    render(<DemoCta />);

    await userEvent.click(screen.getByRole("button", { name: /enter the vault/i }));

    expect(demoLogin).toHaveBeenCalledTimes(2); // initial + one retry, no infinite loop
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
  });

  it("anonymous + network error: still navigates (never a dead click)", async () => {
    demoLogin.mockRejectedValue(new TypeError("network down"));
    render(<DemoCta />);

    await userEvent.click(screen.getByRole("button", { name: /enter the vault/i }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
  });

  it("owner (already signed in): goes straight in as themselves, no demo login", async () => {
    h.isAuthenticated = true;
    render(<DemoCta />);

    await userEvent.click(screen.getByRole("button", { name: /enter the vault/i }));

    expect(demoLogin).not.toHaveBeenCalled();
    expect(assign).toHaveBeenCalledWith("/collection");
  });

  it("does NOT demo-login while auth is still loading (owner-demotion guard)", async () => {
    // During the cold /me probe, isAuthenticated is transiently false; the button must be
    // inert so a returning owner can't demote their own session to the demo.
    h.isLoading = true;
    render(<DemoCta />);

    const button = screen.getByRole("button", { name: /enter the vault/i });
    expect(button).toBeDisabled();
    await userEvent.click(button);

    expect(demoLogin).not.toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });

  it("shows the caption to a settled anonymous visitor", () => {
    render(<DemoCta caption="Live, read-only demo — no sign-in needed." />);
    expect(screen.getByText(/no sign-in needed/i)).toBeInTheDocument();
  });

  it("hides the caption from a signed-in owner (the framing is false for them)", () => {
    h.isAuthenticated = true;
    render(<DemoCta caption="Live, read-only demo — no sign-in needed." />);
    expect(screen.queryByText(/no sign-in needed/i)).not.toBeInTheDocument();
  });

  it("hides the caption while auth is still loading (no flash)", () => {
    h.isLoading = true;
    render(<DemoCta caption="Live, read-only demo — no sign-in needed." />);
    expect(screen.queryByText(/no sign-in needed/i)).not.toBeInTheDocument();
  });
});
