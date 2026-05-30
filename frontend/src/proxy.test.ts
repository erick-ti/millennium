// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { proxy } from "./proxy";

function makeRequest(method: string, cookie?: string): NextRequest {
  const headers = new Headers();
  if (cookie) headers.set("cookie", cookie);
  return new NextRequest("http://localhost:3000/api/imports/batches/", {
    method,
    headers,
  });
}

// NextResponse.next({ request: { headers } }) propagates request headers upstream
// via Next's `x-middleware-request-*` convention (+ an `x-middleware-override-headers`
// manifest). Asserting on those is how request-header rewriting is observed in tests.
describe("proxy CSRF injection", () => {
  it("copies the csrftoken cookie into X-CSRFToken on unsafe methods", () => {
    const response = proxy(makeRequest("POST", "csrftoken=tok-123; sessionid=abc"));
    expect(response.headers.get("x-middleware-request-x-csrftoken")).toBe("tok-123");
    expect(response.headers.get("x-middleware-override-headers")).toContain("x-csrftoken");
  });

  it("leaves safe methods untouched (no token needed)", () => {
    const response = proxy(makeRequest("GET", "csrftoken=tok-123"));
    expect(response.headers.get("x-middleware-request-x-csrftoken")).toBeNull();
  });

  it("forwards unchanged when there is no csrftoken cookie", () => {
    const response = proxy(makeRequest("POST"));
    expect(response.headers.get("x-middleware-request-x-csrftoken")).toBeNull();
  });
});
