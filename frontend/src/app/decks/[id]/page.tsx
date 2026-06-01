import { notFound } from "next/navigation";

import { DeckDetail } from "@/components/decks/deck-detail";

/**
 * Deck detail route. A Server Component awaits the (async, Next 16) `params`,
 * validates the id, and hands the numeric id to the `"use client"` island that
 * owns the TanStack Query data fetching + write mutations. The server boundary
 * lets us `notFound()` a non-numeric id up front (the cards/portfolios pattern).
 */
export default async function DeckDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const deckId = Number(id);
  if (!Number.isInteger(deckId) || deckId <= 0) {
    notFound();
  }
  return <DeckDetail deckId={deckId} />;
}
