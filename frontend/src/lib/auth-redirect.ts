import { ApiError } from "@/lib/auth-interceptor";

/**
 * Send the user to the dedicated /login route, preserving where they were as
 * `?next=`. A HARD navigation (not `router.push`): this fires from the
 * `QueryCache` (outside React — no hook/router context), and crossing the auth
 * boundary should tear down all cached private data, which a soft push would
 * keep. No-ops on the server and when already on /login (never loop).
 */
export function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  const { pathname, search } = window.location;
  if (pathname === "/login") return;
  const next = encodeURIComponent(pathname + search);
  window.location.assign(`/login?next=${next}`);
}

/**
 * Global read-auth gate, wired into the `QueryCache` `onError`. An unauthenticated
 * request in this DRF stack is 403 (there is no 401), and every endpoint is
 * `IsAuthenticated`, so a 403 on a read means "no session" → sign in.
 *
 * EXEMPT: the `/api/auth/me` probe (its 403 is the expected anonymous signal the
 * `AuthProvider` consumes — redirecting on it would loop; excluded both by its
 * `meta` flag and defensively by URL) and anything already on /login. Network /
 * other errors are NOT `ApiError`s (the interceptor only wraps HTTP responses),
 * so they stay on the page where the view's own `QueryErrorState` offers Retry.
 */
export function handleQueryAuthError(error: unknown, isMeProbe: boolean): void {
  if (!(error instanceof ApiError)) return;
  if (error.status !== 401 && error.status !== 403) return;
  if (isMeProbe || error.url?.includes("/api/auth/me")) return;
  redirectToLogin();
}

const MAX_QUERY_RETRIES = 3;

/**
 * Query retry predicate for the global `QueryClient`. A 401/403 is a definitive
 * auth failure that `handleQueryAuthError` will redirect to /login, so it must NOT
 * be retried — `QueryCache.onError` only fires after the retryer gives up, so
 * retrying a 403 would make an anonymous/expired-session user wait through 3 doomed
 * attempts + backoff (~7s) on a protected page before the redirect. Transient /
 * network / 5xx errors still get limited retries so a flaky connection self-heals.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return false;
  }
  return failureCount < MAX_QUERY_RETRIES;
}
