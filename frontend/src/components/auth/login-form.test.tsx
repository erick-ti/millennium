import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { authLoginCreate } from "@/lib/api";

import { LoginForm } from "./login-form";

// Controllable router + search params + auth state, shared with the hoisted mocks.
const h = vi.hoisted(() => ({
  replace: vi.fn(),
  search: "",
  isAuthenticated: false,
  isDemo: false,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: h.replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(h.search),
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: null,
    isLoading: false,
    isAuthenticated: h.isAuthenticated,
    isDemo: h.isDemo,
    canWrite: h.isAuthenticated && !h.isDemo,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  authLoginCreate: vi.fn(),
  authMeRetrieveQueryKey: vi.fn(() => [{ _id: "authMeRetrieve" }]),
  csrfRetrieve: vi.fn(async () => ({})),
}));

const loginMock = vi.mocked(authLoginCreate);

type LoginResult = Awaited<ReturnType<typeof authLoginCreate>>;

function resolved(value: {
  data?: { id: number; username: string; email: string };
  response?: { status: number };
}): LoginResult {
  return value as unknown as LoginResult;
}

function renderForm() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <LoginForm />
    </QueryClientProvider>,
  );
  return { ...utils, invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  h.search = "";
  h.isAuthenticated = false;
  h.isDemo = false;
});

describe("LoginForm", () => {
  it("renders username + password fields and a submit button", () => {
    renderForm();
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("submits the entered credentials to the login fn", async () => {
    loginMock.mockResolvedValue(
      resolved({ data: { id: 1, username: "reader", email: "r@x" }, response: { status: 200 } }),
    );
    renderForm();

    await userEvent.type(screen.getByLabelText(/username/i), "reader");
    await userEvent.type(screen.getByLabelText(/password/i), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() =>
      expect(loginMock).toHaveBeenCalledWith({
        body: { username: "reader", password: "hunter2" },
      }),
    );
  });

  it("shows an inline error on bad credentials (400) and does NOT navigate", async () => {
    loginMock.mockResolvedValue(resolved({ response: { status: 400 } }));
    renderForm();

    await userEvent.type(screen.getByLabelText(/username/i), "reader");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(
      await screen.findByText(/incorrect username or password/i),
    ).toBeInTheDocument();
    expect(h.replace).not.toHaveBeenCalled();
  });

  it("shows a rate-limit message on 429 and does NOT navigate", async () => {
    loginMock.mockResolvedValue(resolved({ response: { status: 429 } }));
    renderForm();

    await userEvent.type(screen.getByLabelText(/username/i), "reader");
    await userEvent.type(screen.getByLabelText(/password/i), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(
      await screen.findByText(/too many sign-in attempts/i),
    ).toBeInTheDocument();
    expect(h.replace).not.toHaveBeenCalled();
  });

  it("on success invalidates the session probe and redirects to ?next", async () => {
    h.search = "next=/collection";
    loginMock.mockResolvedValue(
      resolved({ data: { id: 1, username: "reader", email: "r@x" }, response: { status: 200 } }),
    );
    const { invalidateSpy } = renderForm();

    await userEvent.type(screen.getByLabelText(/username/i), "reader");
    await userEvent.type(screen.getByLabelText(/password/i), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(h.replace).toHaveBeenCalledWith("/collection"));
    expect(invalidateSpy).toHaveBeenCalled();
  });

  it("redirects to the app home (not the nav-less landing) when there is no ?next", async () => {
    // h.search is "" (the beforeEach default) — a direct visit to /login. The
    // landing ("/") hides the app nav, so a signed-in user must land in the app.
    loginMock.mockResolvedValue(
      resolved({ data: { id: 1, username: "reader", email: "r@x" }, response: { status: 200 } }),
    );
    renderForm();

    await userEvent.type(screen.getByLabelText(/username/i), "reader");
    await userEvent.type(screen.getByLabelText(/password/i), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(h.replace).toHaveBeenCalledWith("/collection"));
  });

  // Each of these resolves OFF-ORIGIN via the WHATWG URL parser, so the guard
  // must reject all of them and fall back to the app home (/collection), never
  // off-origin.
  it.each([
    ["absolute URL", "next=https://evil.test"],
    ["protocol-relative", "next=//evil.test"],
    ["backslash authority", "next=/\\evil.test"],
    ["double backslash", "next=/\\\\evil.test"],
  ])("ignores an unsafe ?next (%s) and falls back to the app home", async (_label, search) => {
    h.search = search;
    loginMock.mockResolvedValue(
      resolved({ data: { id: 1, username: "reader", email: "r@x" }, response: { status: 200 } }),
    );
    renderForm();

    await userEvent.type(screen.getByLabelText(/username/i), "reader");
    await userEvent.type(screen.getByLabelText(/password/i), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(h.replace).toHaveBeenCalledWith("/collection"));
  });

  it("bounces away immediately if already authenticated", async () => {
    h.isAuthenticated = true;
    h.search = "next=/cards";
    renderForm();

    await waitFor(() => expect(h.replace).toHaveBeenCalledWith("/cards"));
  });

  it("does NOT bounce a demo session — it may reach the form to upgrade to owner", async () => {
    h.isAuthenticated = true;
    h.isDemo = true;
    h.search = "next=/cards";
    renderForm();

    // The form renders (so a demo user can sign in as the owner); no redirect fires.
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
    expect(h.replace).not.toHaveBeenCalled();
  });
});
