"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { authLoginCreate, authMeRetrieveQueryKey } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { WadjetEye } from "@/components/brand/wadjet-eye";
import { useAuth } from "@/components/auth-provider";
import { seedCsrf } from "@/lib/csrf";

// Where a freshly-signed-in user lands when there's no (or an unsafe) `?next=`
// — e.g. they opened /login directly. NOT "/": that's the public landing, which
// hides the app nav, so a signed-in user dropped there has no chrome to reach
// the app. An explicit `?next=` (e.g. the deep link the auth gate preserves) is
// still honored.
const POST_LOGIN_HOME = "/collection";

/**
 * Only a same-site relative path is a safe post-login destination (open-redirect
 * guard). Must reject anything the WHATWG URL parser would resolve off-origin: a
 * scheme-relative `//host`, AND a backslash variant `/\host` or `/\\host` (the
 * URL parser treats `\` like `/` in the authority, so `new URL("/\\evil.com",
 * origin)` resolves to `https://evil.com`). Accept only a leading `/` NOT
 * followed by another `/` or `\`; otherwise fall back to the app home.
 */
function safeNext(next: string | null): string {
  if (next && /^\/(?![/\\])/.test(next)) return next;
  return POST_LOGIN_HOME;
}

// Use the bare SDK fn (not the *Mutation helper) so we read response.status: a
// 400 is bad credentials (shown inline), a 403 is a missing/stale CSRF cookie
// (re-seed + retry). Login is AllowAny, so a 403 is never "no session".
async function submitLogin(credentials: {
  username: string;
  password: string;
}): Promise<void> {
  const { data, response } = await authLoginCreate({ body: credentials });
  if (data) return;
  if (response?.status === 403) {
    seedCsrf();
    throw new Error("Your session check expired — please try again.");
  }
  if (response?.status === 400) {
    throw new Error("Incorrect username or password.");
  }
  if (response?.status === 429) {
    throw new Error("Too many sign-in attempts. Please wait a minute and try again.");
  }
  throw new Error(
    response
      ? `Sign-in failed (HTTP ${response.status}).`
      : "Sign-in failed: could not reach the server.",
  );
}

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { isAuthenticated } = useAuth();
  const next = safeNext(searchParams.get("next"));

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: submitLogin,
    onSuccess: async () => {
      // Refresh the session probe so the nav reflects the signed-in user, then go.
      await queryClient.invalidateQueries({ queryKey: authMeRetrieveQueryKey() });
      router.replace(next);
    },
  });

  // Already signed in (e.g. navigated to /login manually, or just logged in) →
  // bounce to the target. Navigation is a side effect, so it lives in an effect.
  useEffect(() => {
    if (isAuthenticated) router.replace(next);
  }, [isAuthenticated, next, router]);

  return (
    <div className="mx-auto flex max-w-sm flex-col gap-6 px-6 py-16">
      {/* Signature touch: the brand Eye over the vault entrance — STATIC here
          (no animate / no live pulse): the login screen should feel calm, not
          like a status light. */}
      <WadjetEye className="mx-auto w-12" />

      <div className="vitrine mx-auto w-full max-w-sm rounded-lg p-5 sm:p-6">
        <p className="font-terminal text-xs uppercase tracking-[0.2em] text-gold-700">
          Authenticate
        </p>
        <h1 className="mt-2 font-display text-2xl text-bone">
          Sign in to Millennium
        </h1>
        <hr className="gold-rule mt-4" />

        <form
          className="mt-5 flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate({ username, password });
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-bone">Username</span>
            <input
              name="username"
              type="text"
              autoComplete="username"
              autoFocus
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              disabled={mutation.isPending}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm text-bone outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-bone">Password</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={mutation.isPending}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm text-bone outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
            />
          </label>

          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "Signing in…" : "Sign in"}
          </Button>

          {mutation.isError ? (
            <p role="alert" className="text-sm text-loss">
              {mutation.error.message}
            </p>
          ) : null}
        </form>
      </div>
    </div>
  );
}
