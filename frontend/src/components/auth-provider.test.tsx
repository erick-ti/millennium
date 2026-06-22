import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { User } from "@/lib/api";
import { authMeRetrieveOptions } from "@/lib/api";

import { AuthProvider, useAuth } from "./auth-provider";

// Only the me-probe option factory is mocked; the query-key helper is a stub.
vi.mock("@/lib/api", () => ({
  authMeRetrieveOptions: vi.fn(),
  authMeRetrieveQueryKey: vi.fn(() => [{ _id: "authMeRetrieve" }]),
}));

const meOptions = vi.mocked(authMeRetrieveOptions);

function stubMe(queryFn: () => Promise<User>) {
  meOptions.mockReturnValue({
    queryKey: [{ _id: "authMeRetrieve" }],
    queryFn,
  } as unknown as ReturnType<typeof authMeRetrieveOptions>);
}

function Probe() {
  const { user, isAuthenticated, isLoading, isDemo, canWrite } = useAuth();
  return (
    <>
      <span data-testid="state">
        {isLoading
          ? "loading"
          : isAuthenticated
            ? `authed:${user?.username}`
            : "anon"}
      </span>
      <span data-testid="caps">{`demo:${isDemo} write:${canWrite}`}</span>
    </>
  );
}

function renderProvider() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AuthProvider", () => {
  it("exposes the authenticated user when /me returns 200", async () => {
    stubMe(async () => ({
      id: 1,
      username: "reader",
      email: "r@example.com",
      is_demo: false,
    }));
    renderProvider();
    expect(await screen.findByText("authed:reader")).toBeInTheDocument();
  });

  it("derives the read-only demo state from the server is_demo flag", async () => {
    stubMe(async () => ({ id: 9, username: "demo", email: "", is_demo: true }));
    renderProvider();
    expect(await screen.findByText("authed:demo")).toBeInTheDocument();
    expect(screen.getByTestId("caps")).toHaveTextContent("demo:true write:false");
  });

  it("treats a real (non-demo) user as a writer", async () => {
    stubMe(async () => ({ id: 1, username: "reader", email: "", is_demo: false }));
    renderProvider();
    expect(await screen.findByText("authed:reader")).toBeInTheDocument();
    expect(screen.getByTestId("caps")).toHaveTextContent("demo:false write:true");
  });

  it("is unauthenticated (not a crash) when /me errors — the anonymous 403", async () => {
    stubMe(async () => {
      throw new Error("403");
    });
    renderProvider();
    expect(await screen.findByText("anon")).toBeInTheDocument();
  });

  it("starts in a loading state before /me resolves", () => {
    stubMe(() => new Promise<User>(() => {})); // never resolves
    renderProvider();
    expect(screen.getByTestId("state")).toHaveTextContent("loading");
  });

  it("throws if useAuth is used outside the provider", () => {
    function Orphan() {
      useAuth();
      return null;
    }
    // Silence the React error-boundary console noise for this expected throw.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Orphan />)).toThrow(/within <AuthProvider>/);
    spy.mockRestore();
  });
});
