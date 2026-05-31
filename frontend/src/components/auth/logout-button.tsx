"use client";

import { useMutation } from "@tanstack/react-query";

import { authLogoutCreate } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { seedCsrf } from "@/lib/csrf";

/** True when a 403's body is a CSRF failure (the session is still valid, the
 *  token is stale). Distinguished from an *auth* 403 ("credentials not provided"),
 *  which means the session is already gone. */
function isCsrfFailure(body: unknown): boolean {
  const detail =
    body && typeof body === "object" && "detail" in body
      ? String((body as { detail: unknown }).detail)
      : "";
  return detail.toLowerCase().includes("csrf");
}

/**
 * Resolves ONLY when the user is genuinely signed out — server-confirmed (2xx) or
 * an auth-403 proving the session was already gone — so the mutation's `onSuccess`
 * can safely clear local state + redirect. Throws (keeping the cache + offering a
 * retry) whenever the outcome is unknown, so a failed sign-out never masquerades
 * as a successful one:
 *   - a stale-CSRF 403 → re-seed first (the session is still valid; clearing would
 *     bounce a signed-in user back in);
 *   - a 5xx / network failure (no response) / other status → `logout()` may never
 *     have run server-side, so we must NOT pretend it did.
 */
async function submitLogout(): Promise<void> {
  const { data, error, response } = await authLogoutCreate();
  if (data || response?.ok) return; // server confirmed sign-out
  if (response?.status === 403) {
    if (isCsrfFailure(error)) {
      seedCsrf(); // stale token, session still valid — re-seed and let the user retry
      throw new Error("Sign-out failed — please try again.");
    }
    return; // auth-403: the session is already gone → complete the local sign-out
  }
  // 5xx / network (no response) / other: outcome unknown — keep state, offer retry.
  throw new Error("Sign-out failed — please try again.");
}

export function LogoutButton() {
  const mutation = useMutation({
    mutationFn: submitLogout,
    // HARD navigation (not router.push + queryClient.clear()): a full reload tears
    // down the entire client — cache AND mounted observers — so no stale auth state
    // survives the sign-out. clear() alone does NOT reset a mounted observer
    // (verified: AuthProvider's /me keeps the old user and doesn't refetch), so a
    // soft nav would let /login's already-authenticated effect bounce the user
    // right back in. Same teardown rationale as the global 403 redirect.
    onSuccess: () => window.location.assign("/login"),
  });

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
      >
        {mutation.isPending
          ? "Signing out…"
          : mutation.isError
            ? "Retry sign-out"
            : "Sign out"}
      </Button>
      {mutation.isError ? (
        <span role="alert" className="sr-only">
          {mutation.error.message}
        </span>
      ) : null}
    </>
  );
}
