import { DetailSkeleton } from "@/components/cards/detail-skeleton";

/**
 * Route-shell skeleton for the deck detail page. Covers the route render; the
 * island's `useQuery` pending branch reuses the same `DetailSkeleton label="deck"`
 * so the two can't drift (review K4).
 */
export default function Loading() {
  return <DetailSkeleton label="deck" />;
}
