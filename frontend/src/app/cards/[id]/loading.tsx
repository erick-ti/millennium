import { DetailSkeleton } from "@/components/cards/detail-skeleton";

/**
 * Route-shell skeleton for the card detail page. Next 16 recommends a
 * `loading.tsx` on dynamic routes to enable partial prefetching + an instant
 * navigation transition; it covers the route render, while the island's
 * `useQuery` pending state covers the data fetch (both reuse `DetailSkeleton`
 * so the two can't drift — review K4).
 */
export default function Loading() {
  return <DetailSkeleton />;
}
