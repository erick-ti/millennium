import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/auth-interceptor";
import { reportClientError } from "@/lib/report-error";

import { makeQueryClient } from "./providers";

vi.mock("@/lib/report-error", () => ({ reportClientError: vi.fn() }));
const reportMock = vi.mocked(reportClientError);

// Integration test of the REAL QueryClient wiring (the retry predicate + the
// QueryCache onError redirect), which the mocked page tests never exercise —
// each of those builds its own retry:false client. This proves a 403 read fails
// fast (no doomed retries) AND redirects to /login.
const assign = vi.fn();
const realLocation = window.location;

function stubLocation(pathname: string, search = "") {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname, search, href: `http://localhost${pathname}${search}`, assign },
  });
}

function Probe({
  queryFn,
  queryKey = "probe",
  retry,
}: {
  queryFn: () => Promise<unknown>;
  queryKey?: string;
  retry?: boolean;
}) {
  useQuery({ queryKey: [queryKey], queryFn, ...(retry !== undefined ? { retry } : {}) });
  return null;
}

beforeEach(() => {
  assign.mockClear();
  stubLocation("/collection");
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

describe("makeQueryClient (real provider wiring)", () => {
  it("redirects a 403 read to /login after exactly ONE fetch (no retries)", async () => {
    const queryFn = vi.fn(async () => {
      throw new ApiError(403, "https://app.test/api/collection/items/", {
        detail: "Authentication credentials were not provided.",
      });
    });
    const queryClient = makeQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <Probe queryFn={queryFn} />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("/login?next=%2Fcollection"),
    );
    // The retry predicate fast-fails a 403, so the doomed query runs once, not 4×.
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("beacons a proxy 5xx (never reaches Django) as a frontend error", async () => {
    reportMock.mockClear();
    const queryFn = vi.fn(async () => {
      throw new ApiError(502, "https://app.test/api/cards/cards/", null);
    });
    const queryClient = makeQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <Probe queryFn={queryFn} queryKey="proxy-502" retry={false} />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(reportMock).toHaveBeenCalledTimes(1));
  });

  it("does not beacon an expected 4xx", async () => {
    reportMock.mockClear();
    const queryFn = vi.fn(async () => {
      throw new ApiError(404, "https://app.test/api/cards/cards/999/", null);
    });
    const queryClient = makeQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <Probe queryFn={queryFn} queryKey="expected-404" retry={false} />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(queryFn).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(reportMock).not.toHaveBeenCalled();
  });
});
