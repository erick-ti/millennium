import { describe, expect, it } from "vitest";

import { ApiError, toApiError } from "@/lib/auth-interceptor";

// The interceptor's throwOnError gate is the slice's blast-radius linchpin: it
// must wrap ONLY throwing (query) callers into an ApiError carrying the status,
// while leaving non-throwing (write) callers' raw bodies untouched so existing
// DRF field-error / 409 parsing keeps working. A gate inversion here would
// silently disable the global 403→/login redirect.
describe("toApiError (interceptor blast-radius gate)", () => {
  it("wraps a throwing (query) caller's error into an ApiError with status + url", () => {
    const result = toApiError(
      { detail: "Authentication credentials were not provided." },
      { status: 403 },
      { url: "https://app.test/api/collection/items/" },
      true,
    );
    expect(result).toBeInstanceOf(ApiError);
    expect((result as ApiError).status).toBe(403);
    expect((result as ApiError).url).toBe("https://app.test/api/collection/items/");
    expect((result as ApiError).message).toMatch(/credentials were not provided/i);
  });

  it("returns a non-throwing (write) caller's raw error UNTOUCHED (same reference)", () => {
    const raw = { file: ["file exceeds the 10 MB upload limit"] };
    const result = toApiError(raw, { status: 400 }, { url: "/api/imports/batches/" }, false);
    // Same object reference — import-upload's fieldError still reads `{ file }`.
    expect(result).toBe(raw);
    expect(result).not.toBeInstanceOf(ApiError);
  });

  it("treats undefined throwOnError as non-throwing (raw body preserved)", () => {
    const raw = { detail: "conflict" };
    expect(toApiError(raw, { status: 409 }, { url: "/api/x/" }, undefined)).toBe(raw);
  });

  it("tolerates a missing response/request (network error before a response)", () => {
    const result = toApiError("boom", undefined, undefined, true);
    expect(result).toBeInstanceOf(ApiError);
    expect((result as ApiError).status).toBeUndefined();
    expect((result as ApiError).url).toBeUndefined();
  });
});
