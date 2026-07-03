/**
 * Frontend error beacon → `POST /api/audit/client-errors/` (apps.audit, commit 2).
 *
 * Deliberately dependency-light: a RAW `fetch`, never the generated SDK client, so it still
 * works when the error originated in the api layer itself. The request goes through the Next
 * `/api` proxy, which injects `X-CSRFToken` on unsafe methods, so there is no manual CSRF
 * handling here. Fire-and-forget: it never throws and never surfaces its own failure.
 */

const ENDPOINT = "/api/audit/client-errors/";
// Seeds the csrftoken cookie (GET /api/csrf/) — a raw fetch, so the reporter stays
// dependency-light. Used to recover from a CSRF-bootstrap race (see the 403 retry below).
const CSRF_ENDPOINT = "/api/csrf/";
// Politeness caps so a render loop can't flood the beacon (and the backend throttle).
const MAX_REPORTS_PER_LOAD = 20;
const MAX_STACK = 8_000;

let sent = 0;
const seen = new Set<string>();

export type ClientErrorReport = {
  message: string;
  name?: string;
  stack?: string;
  /** Page path; defaults to window.location.pathname (never a query string). */
  url?: string;
  /** X-Request-ID of the failed API call, when known, for backend correlation. */
  requestId?: string;
};

/** Reset module state — test-only (the per-load caps persist across a real session). */
export function __resetReporterForTest(): void {
  sent = 0;
  seen.clear();
}

function postBeacon(body: string): Promise<Response> {
  return fetch(ENDPOINT, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body,
    // Let the beacon complete even if the page is unloading/navigating away.
    keepalive: true,
  });
}

export function reportClientError(report: ClientErrorReport): void {
  try {
    if (typeof window === "undefined") return;

    const message = (report.message ?? "").trim();
    if (!message) return;
    if (sent >= MAX_REPORTS_PER_LOAD) return;

    const key = `${report.name ?? ""}|${message}`;
    if (seen.has(key)) return;

    // Optimistically dedupe a FLOOD of the same error while the beacon is in flight, but roll
    // back on a failed send so a dropped report can be re-reported. `fetch` RESOLVES (does not
    // reject) on a 403/5xx, so `.catch` alone would treat a rejected-by-Django beacon as sent
    // and suppress every repeat — we must inspect the status.
    seen.add(key);
    sent += 1;
    const rollback = () => {
      seen.delete(key);
      sent = Math.max(0, sent - 1);
    };

    const body = JSON.stringify({
      message,
      name: report.name ?? "",
      stack: (report.stack ?? "").slice(0, MAX_STACK),
      url: report.url ?? window.location.pathname,
      request_id: report.requestId ?? "",
    });

    void postBeacon(body)
      .then(async (response) => {
        if (response.ok) return; // 204 — recorded.
        rollback();
        // A CSRF-bootstrap race (the csrftoken cookie wasn't seeded yet) 403s the FIRST crash
        // — exactly the high-value one. Reseed the cookie and retry ONCE so it isn't lost.
        if (response.status === 403) {
          await fetch(CSRF_ENDPOINT, { credentials: "include" }).catch(() => {});
          seen.add(key);
          sent += 1;
          void postBeacon(body).then((retry) => {
            if (!retry.ok) rollback();
          }, rollback);
        }
      })
      .catch(rollback); // Network failure — allow a later re-report; never surface it.
  } catch {
    // Never let reporting break the app.
  }
}
