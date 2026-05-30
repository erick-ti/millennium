import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * CSRF token injection for the same-origin `/api/*` proxy (slice 6, DECISIONS
 * 2026-05-29).
 *
 * Django's DRF `SessionAuthentication` enforces a CSRF token on every unsafe
 * method. The browser holds the token in the (non-HttpOnly) `csrftoken` cookie
 * — seeded by `GET /api/csrf/` on app load — but the generated client is
 * CSRF-naive by design (slice 2 fork 4), so this proxy reads that cookie and
 * copies it into the `X-CSRFToken` request header before the request is
 * forwarded upstream to Django.
 *
 * Ordering matters and works in our favor: Next 16 runs Proxy (step 3) BEFORE
 * the `next.config.ts` rewrites (step 4, `beforeFiles`), so the header we add
 * here is present when the rewrite forwards to Django. This is the inverse of
 * URL-shape canonicalization, which Next runs *before* Proxy — that's why the
 * trailing-slash handling lives in `next.config.ts`, not here.
 *
 * Two non-obvious requirements:
 *   - Use the UPSTREAM form `NextResponse.next({ request: { headers } })`. The
 *     response-header form (`response.headers.set(...)`) would send the header
 *     to the *client*, not to Django, and the POST would still 403.
 *   - `request.cookies.get(...)` returns a `{ name, value }` object — read
 *     `.value`, or you'd inject `[object Object]`.
 *
 * The `CSRF_TRUSTED_ORIGINS` Origin check is a separate CSRF gate handled by
 * Django settings (Invariant 10); this proxy only supplies the token half.
 */

// Methods Django's CsrfViewMiddleware enforces a token on. GET/HEAD/OPTIONS/TRACE
// are "safe" and need none, so we leave them untouched.
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function proxy(request: NextRequest): NextResponse {
  if (!UNSAFE_METHODS.has(request.method)) {
    return NextResponse.next();
  }

  const csrftoken = request.cookies.get("csrftoken")?.value;
  if (!csrftoken) {
    // No cookie to forward (e.g. the first load before /api/csrf/ resolves).
    // Forward unchanged; Django will 403, and the mutation surfaces it.
    return NextResponse.next();
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("X-CSRFToken", csrftoken);
  return NextResponse.next({ request: { headers: requestHeaders } });
}

// Only run on the proxied API paths (the incoming path, before the rewrite).
export const config = {
  matcher: "/api/:path*",
};
