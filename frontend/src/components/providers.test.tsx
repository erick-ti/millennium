import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/auth-interceptor";

import { makeQueryClient } from "./providers";

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

function Probe({ queryFn }: { queryFn: () => Promise<unknown> }) {
  useQuery({ queryKey: ["integration-403"], queryFn });
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
});
