import { notFound } from "next/navigation";

import { ImportBatchDetail } from "@/components/imports/import-batch-detail";

export default async function ImportBatchPage({
  params,
}: {
  // Next 16: route params are async — must be awaited.
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const batchId = Number(id);
  if (!Number.isInteger(batchId) || batchId <= 0) notFound();
  return <ImportBatchDetail batchId={batchId} />;
}
