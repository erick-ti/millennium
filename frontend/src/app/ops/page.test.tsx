import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useAuth } from "@/components/auth-provider";

import OpsPage from "./page";

vi.mock("@/components/auth-provider", () => ({ useAuth: vi.fn() }));
vi.mock("@/components/ops/error-groups-panel", () => ({
  ErrorGroupsPanel: () => <div data-testid="error-groups-panel" />,
}));
vi.mock("@/components/ops/audit-feed-panel", () => ({
  AuditFeedPanel: () => <div data-testid="audit-feed-panel" />,
}));

const mockUseAuth = vi.mocked(useAuth);

function authState(
  overrides: Partial<ReturnType<typeof useAuth>> = {},
): ReturnType<typeof useAuth> {
  return {
    user: null,
    isLoading: false,
    isAuthenticated: false,
    isDemo: false,
    canWrite: false,
    isSuperuser: false,
    refetch: () => {},
    ...overrides,
  };
}

describe("OpsPage", () => {
  it("renders the console panels for a superuser", () => {
    mockUseAuth.mockReturnValue(
      authState({ isAuthenticated: true, isSuperuser: true }),
    );
    render(<OpsPage />);
    expect(screen.getByTestId("error-groups-panel")).toBeInTheDocument();
    expect(screen.getByTestId("audit-feed-panel")).toBeInTheDocument();
  });

  it("restricts an authenticated non-superuser (demo)", () => {
    mockUseAuth.mockReturnValue(authState({ isAuthenticated: true, isDemo: true }));
    render(<OpsPage />);
    expect(screen.queryByTestId("audit-feed-panel")).not.toBeInTheDocument();
    expect(screen.getByText("Restricted")).toBeInTheDocument();
    expect(screen.getByText(/owner-only/i)).toBeInTheDocument();
  });

  it("prompts an anonymous visitor to sign in", () => {
    mockUseAuth.mockReturnValue(authState());
    render(<OpsPage />);
    expect(screen.getByText("Restricted")).toBeInTheDocument();
    expect(screen.getByText(/sign in as the owner/i)).toBeInTheDocument();
  });
});
