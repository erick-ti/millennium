import { notFound } from "next/navigation";

import { PortfolioDetail } from "@/components/portfolios/portfolio-detail";

/**
 * Portfolio detail route. A Server Component awaits the (async, Next 16)
 * `params`, validates the id, and hands the numeric id to the `"use client"`
 * island that owns the TanStack Query data fetching. The server boundary lets
 * us `notFound()` a non-numeric id up front (mirrors `cards/[id]`).
 */
export default async function PortfolioDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const portfolioId = Number(id);
  if (!Number.isInteger(portfolioId) || portfolioId <= 0) {
    notFound();
  }
  return <PortfolioDetail portfolioId={portfolioId} />;
}
