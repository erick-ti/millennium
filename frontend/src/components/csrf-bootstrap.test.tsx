import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { csrfRetrieve } from "@/lib/api";

import { CsrfBootstrap } from "./csrf-bootstrap";

// CsrfBootstrap is the only client-side trigger that seeds the csrftoken cookie before the
// first POST; if it stopped firing, every write would 403 on first load with green CI.
vi.mock("@/lib/api", () => ({ csrfRetrieve: vi.fn() }));

const csrfMock = vi.mocked(csrfRetrieve);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CsrfBootstrap", () => {
  it("calls csrfRetrieve once on mount to seed the CSRF cookie", () => {
    csrfMock.mockResolvedValue({} as never);
    render(<CsrfBootstrap />);
    expect(csrfMock).toHaveBeenCalledTimes(1);
  });

  it("swallows a failed seed without throwing (fire-and-forget)", async () => {
    csrfMock.mockRejectedValue(new Error("network"));
    expect(() => render(<CsrfBootstrap />)).not.toThrow();
    // Flush microtasks so the component's .catch runs (no unhandled rejection).
    await Promise.resolve();
    expect(csrfMock).toHaveBeenCalledTimes(1);
  });
});
