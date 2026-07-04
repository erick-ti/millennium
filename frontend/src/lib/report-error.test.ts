import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { __resetReporterForTest, reportClientError } from "./report-error";

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("reportClientError", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    __resetReporterForTest();
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs the error to the beacon endpoint with an allowlisted body", () => {
    reportClientError({
      message: "boom",
      name: "TypeError",
      stack: "at x",
      url: "/collection",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/audit/client-errors/");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body).toMatchObject({ message: "boom", name: "TypeError", url: "/collection" });
  });

  it("dedupes identical errors within a load", () => {
    reportClientError({ message: "dup" });
    reportClientError({ message: "dup" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("ignores a blank message", () => {
    reportClientError({ message: "   " });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("never throws when fetch rejects", () => {
    fetchMock.mockRejectedValue(new Error("network down"));
    expect(() => reportClientError({ message: "later" })).not.toThrow();
  });

  it("reseeds CSRF and retries once when the beacon is 403'd (CSRF race)", async () => {
    fetchMock
      .mockResolvedValueOnce({ ok: false, status: 403 }) // first beacon POST — CSRF race
      .mockResolvedValueOnce({ ok: true, status: 200 }) // CSRF reseed GET
      .mockResolvedValueOnce({ ok: true, status: 204 }); // retry beacon POST

    reportClientError({ message: "raced" });

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/audit/client-errors/",
      "/api/csrf/",
      "/api/audit/client-errors/",
    ]);
  });

  it("allows a re-report after a failed (non-403) send — not suppressed forever", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500 });
    reportClientError({ message: "flaky" });
    await flush(); // let the .then(rollback) run

    fetchMock.mockResolvedValue({ ok: true, status: 204 });
    reportClientError({ message: "flaky" });
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
