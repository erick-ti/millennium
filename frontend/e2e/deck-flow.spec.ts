import { expect, test } from "@playwright/test";

import { DECK_CARD_NAME, login } from "./helpers";

// The second flow: create a deck, tag an owned holding into it via the picker,
// and see the member appear. Exercises the deck CRUD writes + the owned-only
// holding picker (the seed gives the deck card a lot, so quantity > 0 and it
// passes the zero-copy guard).
//
// The deck name carries a unique per-run suffix (decks are non-unique by name)
// so the list link + detail heading are unambiguous on retries; seed_smoke
// --reset clears prior "Smoke E2E" decks between runs.
test("login → create a deck → add an owned holding → see it in the deck", async ({
  page,
  context,
}) => {
  await login(page, context);

  const deckName = `Smoke E2E Deck ${Date.now()}`;

  await page.goto("/decks");
  await page.getByLabel("Deck name").fill(deckName);
  await page.getByRole("button", { name: "Create deck" }).click();
  await expect(page.getByText("Deck created.")).toBeVisible();

  // Open the new deck from the list.
  await page.getByRole("link", { name: deckName }).click();
  await expect(page.getByRole("heading", { name: deckName, level: 1 })).toBeVisible();

  // Add the pre-owned holding via the search picker (debounced; auto-wait on the
  // result button handles the delay — no fixed timeout).
  await page.getByRole("button", { name: "Add holdings" }).click();
  await page.getByLabel("Search your collection by card name").fill(DECK_CARD_NAME);
  await page.getByRole("button", { name: new RegExp(DECK_CARD_NAME) }).click();
  await expect(page.getByText(`Added ${DECK_CARD_NAME} to the deck.`)).toBeVisible();

  // The holding now appears as a member row.
  await expect(page.getByRole("cell", { name: DECK_CARD_NAME })).toBeVisible();
});
