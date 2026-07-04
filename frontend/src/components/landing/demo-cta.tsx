"use client";

import { useState } from "react";

import { authDemoLoginCreate, csrfRetrieve } from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { cn } from "@/lib/utils";

const APP_HOME = "/collection";

/**
 * Landing CTA: one click into the full LIVE app, replacing the old
 * ``<a href="/collection">`` that dead-ended an anonymous visitor at a bare /login.
 *
 * Anonymous visitors are signed into the read-only demo account
 * (``POST /api/auth/demo-login/``; CSRF via proxy.ts, the same pattern used by the login
 * form) and the owner, already signed in, goes straight in as themselves (we never demote
 * their session to the demo). Always HARD-navigates (``window.location``, not
 * ``router.push``) so ``AuthProvider`` re-probes ``/api/auth/me`` with the fresh session,
 * the same pattern the logout hard-nav uses; a soft nav would leave the ``/me`` probe stale
 * and the nav chrome wrong.
 */
export function DemoCta({
  className,
  caption,
}: {
  className?: string;
  /** Optional sub-line (e.g. "no sign-in needed"), shown ONLY to a settled anonymous
   *  visitor, since the framing is false for a signed-in owner who enters as themselves. */
  caption?: string;
}) {
  const { isAuthenticated, isLoading } = useAuth();
  const [pending, setPending] = useState(false);

  async function enter() {
    // Don't act until the /me probe settles: during the cold-load window isAuthenticated
    // is still false, so an OWNER who clicks early would otherwise fall through to demo-login
    // and demote their own session to the demo (the button is also disabled while loading).
    if (pending || isLoading) return;
    if (isAuthenticated) {
      window.location.assign(APP_HOME);
      return;
    }
    setPending(true);
    try {
      const first = await authDemoLoginCreate();
      let data = first.data;
      // A 403 means the csrftoken cookie wasn't seeded yet: CsrfBootstrap's GET raced this
      // click. Re-seed (awaiting csrfRetrieve so the cookie is set) and retry ONCE, the same
      // write-403 recovery used elsewhere (login form, import writes), so an eager
      // click on the headline CTA doesn't dead-end at /login on a transient race.
      if (!data && first.response?.status === 403) {
        await csrfRetrieve();
        data = (await authDemoLoginCreate()).data;
      }
      // Success: the app as the demo; otherwise (demo not seeded, or a persistent failure)
      // the login page is the honest fallback.
      window.location.assign(data ? APP_HOME : "/login");
    } catch {
      window.location.assign("/login");
    }
  }

  const button = (
    <button
      type="button"
      onClick={enter}
      disabled={pending || isLoading}
      className={cn(
        "group inline-flex items-center gap-2.5 rounded-sm px-5 py-3 font-terminal text-xs uppercase tracking-[0.18em] transition-colors disabled:opacity-60",
        className,
      )}
    >
      {pending ? "Entering…" : "Enter the vault"}
      <span className="transition-transform group-hover:translate-x-0.5">→</span>
    </button>
  );

  if (!caption) return button;
  return (
    <>
      {button}
      {/* Gate on the settled anonymous state: a signed-in owner enters as themselves, so
          "no sign-in needed" would be false; and during the cold /me probe the state is
          unknown, so don't flash it then either. */}
      {!isLoading && !isAuthenticated ? (
        <p className="mt-3 font-terminal text-[0.7rem] tracking-wide text-bone-muted">
          {caption}
        </p>
      ) : null}
    </>
  );
}
