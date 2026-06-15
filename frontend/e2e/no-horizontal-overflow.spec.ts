import { expect, test } from "@playwright/test";

import { login } from "./helpers";

// Regression guard for the horizontal-overflow class of bug (a vault-redesign
// sr-only data table was `position:absolute` and grew past the viewport, so the
// whole page scrolled sideways). A page must never be wider than its own
// viewport. Checked at a narrow width (where content-driven overflow is worst)
// and a typical desktop width.
const WIDTHS = [390, 1280];

async function horizontalOverflow(page: import("@playwright/test").Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test.describe("no horizontal overflow", () => {
  for (const width of WIDTHS) {
    test(`the public landing has no horizontal scroll at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await page.waitForLoadState("networkidle");
      // Allow 1px for sub-pixel rounding.
      expect(await horizontalOverflow(page), `landing @ ${width}px overflows`).toBeLessThanOrEqual(1);
    });
  }

  test("authed pages have no horizontal scroll at a narrow width", async ({ page, context }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page, context);

    for (const path of ["/collection", "/cards", "/movers", "/alerts", "/decks", "/imports"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      expect(await horizontalOverflow(page), `${path} @ 390px overflows`).toBeLessThanOrEqual(1);
    }
  });
});
