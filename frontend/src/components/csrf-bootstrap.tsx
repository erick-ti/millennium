"use client";

import { useEffect } from "react";

import { seedCsrf } from "@/lib/csrf";

/**
 * Seed the `csrftoken` cookie once on app load (slice 6). Django mints the token only when a
 * request uses it, and this all-JSON API never renders a form — so without this the SPA has no
 * token for `proxy.ts` to echo into `X-CSRFToken` on its first unsafe request. Fire-and-forget
 * via `seedCsrf()`; a failure here is recoverable because write paths re-seed on a 403
 * (`lib/csrf.ts`). Mounted in the root layout under Providers.
 */
export function CsrfBootstrap() {
  useEffect(() => {
    seedCsrf();
  }, []);

  return null;
}
