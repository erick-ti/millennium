import { notFound } from "next/navigation";

import { CardDetail } from "@/components/cards/card-detail";

/**
 * Card detail route. A Server Component awaits the (async, Next 16) `params`,
 * validates the id, and hands the numeric id to the `"use client"` island that
 * owns the TanStack Query data fetching + selection state. Keeping the data
 * fetching in a client island matches the slice-1 SSR-safe provider setup; the
 * server boundary lets us `notFound()` a non-numeric id up front.
 */
export default async function CardDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const cardId = Number(id);
  if (!Number.isInteger(cardId) || cardId <= 0) {
    notFound();
  }
  return <CardDetail cardId={cardId} />;
}
