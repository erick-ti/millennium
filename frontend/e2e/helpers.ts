import { type BrowserContext, type Page, expect } from "@playwright/test";

// Credentials must match `seed_smoke` (backend/apps/core/management/commands/seed_smoke.py).
export const SMOKE_USER = "smoke";
export const SMOKE_PASSWORD = "smoke-password";

// The card names + the CSV the seed/import flow expects (kept in lockstep with
// seed_smoke.py + e2e/fixtures/smoke-collection.csv).
export const IMPORT_CARD_NAME = "Smoke Import Dragon";
export const DECK_CARD_NAME = "Smoke Deck Token";
export const SMOKE_CSV = "e2e/fixtures/smoke-collection.csv";

/**
 * Log in through the real UI and wait until the session is established.
 *
 * Waits for the `csrftoken` cookie first: <CsrfBootstrap> seeds it via a
 * fire-and-forget GET /api/csrf/ on mount, and proxy.ts needs it to inject
 * X-CSRFToken on the login POST — submitting before it lands intermittently
 * 403s. Then asserts the authenticated nav (the "Sign out" button) is visible,
 * which only renders after the /api/auth/me probe resolves.
 */
export async function login(page: Page, context: BrowserContext): Promise<void> {
  await page.goto("/login");

  await expect
    .poll(async () => (await context.cookies()).some((c) => c.name === "csrftoken"), {
      message: "CSRF cookie was never seeded by <CsrfBootstrap>",
    })
    .toBeTruthy();

  await page.getByLabel("Username").fill(SMOKE_USER);
  await page.getByLabel("Password").fill(SMOKE_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}
