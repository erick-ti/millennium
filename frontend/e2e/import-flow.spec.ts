import { expect, test } from "@playwright/test";

import { IMPORT_CARD_NAME, SMOKE_CSV, login } from "./helpers";

// The highest-value smoke path: it exercises auth + CSRF, the synchronous
// import, the review-queue state machine, the first browser write (approve →
// materialize), and a read view — all in one flow.
//
// The seeded import-target printing matches the CSV row EXACT, but with no
// same-day TCGCSV reconciliation it stages PENDING (approvable) rather than
// auto-materializing — so there is always a row to Approve.
test("login → upload a Dragon Shield CSV → approve the row → see the card in the collection", async ({
  page,
  context,
}) => {
  await login(page, context);

  await page.goto("/imports");
  await page.getByLabel("Dragon Shield CSV file").setInputFiles(SMOKE_CSV);
  await page.getByRole("button", { name: "Import CSV" }).click();

  // The synchronous import finished; follow the "Review →" link to the batch.
  const review = page.getByRole("link", { name: "Review →" });
  await expect(review).toBeVisible();
  await review.click();

  // Approve the staged row. A human approve overrides the freshness gate and
  // materializes the holding into the collection.
  const approve = page.getByRole("button", { name: "Approve" });
  await expect(approve).toBeEnabled();
  await approve.click();
  // Two success banners both start with "Approved": "…added to your collection."
  // on a fresh materialize, or "…already imported unchanged…" if a CI retry
  // re-approves a holding a prior attempt already materialized (the suite reseeds
  // once per run, not per retry). A 409 cost-conflict banner does NOT start with
  // "Approved", so this still catches a real failure; the /collection assertion
  // below verifies the true end-state regardless.
  await expect(page.getByText(/^Approved/)).toBeVisible();

  // The approved card now shows in the collection.
  await page.goto("/collection");
  await expect(page.getByRole("cell", { name: IMPORT_CARD_NAME })).toBeVisible();
});
