import { csrfRetrieve } from "@/lib/api";

/**
 * Seed (or re-seed) the `csrftoken` cookie via `GET /api/csrf/`. Fire-and-forget: a failure is
 * swallowed (the cookie just isn't refreshed this attempt) and never throws.
 *
 * Called in two places (Codex review 2026-05-30):
 *  - once on app load (CsrfBootstrap), to seed before the first unsafe request; and
 *  - after any write 403, because a 403 can mean the cookie was missing or stale (e.g. the
 *    on-mount seed raced the first action or transiently failed). Re-seeding lets the user's
 *    NEXT attempt carry a valid token WITHOUT a full page reload — closing the "swallowed seed
 *    failure → every write 403s until reload" gap.
 *
 * `GET /api/csrf/` is `AllowAny` and a safe method (CSRF-exempt), so this never itself 403s —
 * no re-seed loop. (For an auth 403 — no session — re-seeding is a harmless no-op; the backend's
 * detail message still tells the user to sign in.)
 */
export function seedCsrf(): void {
  // Promise.resolve(...) so a mock or any non-promise return can't throw synchronously.
  void Promise.resolve(csrfRetrieve()).catch(() => {
    // Non-fatal: the cookie just won't be (re)seeded this attempt.
  });
}
