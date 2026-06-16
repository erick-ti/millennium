import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authLogoutCreate, csrfRetrieve } from "@/lib/api";

import { Nav } from "./nav";

const h = vi.hoisted(() => ({
  isAuthenticated: true,
  username: "reader",
  pathname: "/collection",
}));

// LogoutButton no longer uses useRouter (it hard-navigates), but Nav's <Link>s
// resolve a router internally — keep a minimal stub. Nav also reads usePathname
// to hide itself on the landing route ("/"), so expose a controllable value.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => h.pathname,
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: { id: 1, username: h.username, email: "" },
    isLoading: false,
    isAuthenticated: h.isAuthenticated,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  authLogoutCreate: vi.fn(),
  csrfRetrieve: vi.fn(async () => ({})),
}));

const logoutMock = vi.mocked(authLogoutCreate);
const csrfMock = vi.mocked(csrfRetrieve);

type LogoutResult = Awaited<ReturnType<typeof authLogoutCreate>>;

function resolved(value: {
  data?: unknown;
  error?: unknown;
  response?: { status: number; ok: boolean };
}): LogoutResult {
  return value as unknown as LogoutResult;
}

// Logout hard-navigates via window.location.assign — stub it (jsdom's is a noop).
const assign = vi.fn();
const realLocation = window.location;

function renderNav() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Nav />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  h.isAuthenticated = true;
  h.username = "reader";
  h.pathname = "/collection";
  assign.mockClear();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname: "/collection", search: "", href: "http://localhost/collection", assign },
  });
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

describe("Nav", () => {
  it("shows the username and a sign-out button when authenticated", () => {
    renderNav();
    expect(screen.getByText("reader")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /collection/i })).toBeInTheDocument();
  });

  it("hides the user controls when unauthenticated (brand still shown)", () => {
    h.isAuthenticated = false;
    renderNav();
    expect(
      screen.queryByRole("button", { name: /sign out/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("reader")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /millennium/i })).toBeInTheDocument();
  });

  it("renders nothing on the public landing route (it has its own masthead)", () => {
    h.pathname = "/";
    const { container } = renderNav();
    expect(container).toBeEmptyDOMElement();
    expect(
      screen.queryByRole("link", { name: /millennium/i }),
    ).not.toBeInTheDocument();
  });

  it("signs out: calls logout, then HARD-navigates to /login", async () => {
    logoutMock.mockResolvedValue(
      resolved({ data: { detail: "Logged out." }, response: { status: 200, ok: true } }),
    );
    renderNav();

    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));

    // Hard nav (full reload) so no stale auth observer can survive the sign-out.
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
    expect(logoutMock).toHaveBeenCalled();
  });

  it("on an expired-session sign-out (auth 403) still hard-navigates to /login", async () => {
    logoutMock.mockResolvedValue(
      resolved({
        error: { detail: "Authentication credentials were not provided." },
        response: { status: 403, ok: false },
      }),
    );
    renderNav();

    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
  });

  it("on a CSRF-403 sign-out (session still valid) re-seeds + retries, no navigation", async () => {
    logoutMock.mockResolvedValue(
      resolved({ error: { detail: "CSRF Failed: token missing." }, response: { status: 403, ok: false } }),
    );
    renderNav();

    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));

    // Recoverable: flips to a retry affordance; no bounce, no navigation.
    expect(
      await screen.findByRole("button", { name: /retry sign-out/i }),
    ).toBeInTheDocument();
    expect(csrfMock).toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });

  it("on a 5xx sign-out keeps state intact (outcome unknown — no false success)", async () => {
    logoutMock.mockResolvedValue(
      resolved({ error: { detail: "server error" }, response: { status: 500, ok: false } }),
    );
    renderNav();

    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));

    expect(
      await screen.findByRole("button", { name: /retry sign-out/i }),
    ).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("on a network-error sign-out (no response) keeps state intact", async () => {
    logoutMock.mockResolvedValue(resolved({ error: new TypeError("network down") }));
    renderNav();

    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));

    expect(
      await screen.findByRole("button", { name: /retry sign-out/i }),
    ).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });
});
