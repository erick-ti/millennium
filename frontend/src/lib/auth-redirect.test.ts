import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/auth-interceptor";
import { handleQueryAuthError, shouldRetryQuery } from "@/lib/auth-redirect";

const assign = vi.fn();
const realLocation = window.location;

function setLocation(pathname: string, search = "") {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname, search, assign },
  });
}

beforeEach(() => {
  assign.mockClear();
  setLocation("/collection");
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: realLocation,
  });
});

describe("handleQueryAuthError", () => {
  it("redirects a 403 read to /login with the current path as ?next", () => {
    handleQueryAuthError(new ApiError(403, "/api/collection/items/", {}), false);
    expect(assign).toHaveBeenCalledWith("/login?next=%2Fcollection");
  });

  it("preserves the query string in ?next", () => {
    setLocation("/collection", "?page=2");
    handleQueryAuthError(new ApiError(403, "/api/collection/items/", {}), false);
    expect(assign).toHaveBeenCalledWith("/login?next=%2Fcollection%3Fpage%3D2");
  });

  it("does NOT redirect on the /api/auth/me probe (meta flag) — would loop", () => {
    handleQueryAuthError(new ApiError(403, "/api/auth/me/", {}), true);
    expect(assign).not.toHaveBeenCalled();
  });

  it("does NOT redirect on the /api/auth/me probe (URL guard, even without the flag)", () => {
    handleQueryAuthError(new ApiError(403, "/api/auth/me/", {}), false);
    expect(assign).not.toHaveBeenCalled();
  });

  it("does NOT redirect when already on /login (loop guard)", () => {
    setLocation("/login");
    handleQueryAuthError(new ApiError(403, "/api/cards/cards/", {}), false);
    expect(assign).not.toHaveBeenCalled();
  });

  it("does NOT redirect on a non-auth status (e.g. 500)", () => {
    handleQueryAuthError(new ApiError(500, "/api/cards/cards/", {}), false);
    expect(assign).not.toHaveBeenCalled();
  });

  it("does NOT redirect on a non-ApiError (network error / a thrown plain Error)", () => {
    handleQueryAuthError(new Error("403"), false);
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("shouldRetryQuery", () => {
  it("does NOT retry a 401/403 auth failure (fail fast so the redirect isn't delayed)", () => {
    expect(shouldRetryQuery(0, new ApiError(403, "/api/cards/cards/", {}))).toBe(false);
    expect(shouldRetryQuery(0, new ApiError(401, "/api/x/", {}))).toBe(false);
  });

  it("retries transient / 5xx / network errors up to the cap of 3", () => {
    expect(shouldRetryQuery(0, new ApiError(500, "/api/x/", {}))).toBe(true);
    expect(shouldRetryQuery(2, new ApiError(503, "/api/x/", {}))).toBe(true);
    expect(shouldRetryQuery(3, new ApiError(500, "/api/x/", {}))).toBe(false); // cap reached
    expect(shouldRetryQuery(0, new Error("network down"))).toBe(true); // non-ApiError
  });
});
