"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";

import { type ImportBatch, importsBatchesCreate } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { BatchStatusPill } from "@/components/imports/status";
import { seedCsrf } from "@/lib/csrf";

/** Pull a DRF field-error string (`{file: ["..."]}`) out of an unknown error body. */
function fieldError(error: unknown, field: string): string | null {
  if (error && typeof error === "object" && field in error) {
    const value = (error as Record<string, unknown>)[field];
    if (Array.isArray(value) && typeof value[0] === "string") return value[0];
    if (typeof value === "string") return value;
  }
  return null;
}

// Use the SDK fn (not the *Mutation helper) so we read response.status and the
// validation body directly. A non-DS file still returns 201 with status=failed
// (a recorded batch), so only a real 4xx (missing/oversized/non-text file) lands
// here as a thrown error; the failed-parse case flows through onSuccess.
async function uploadCsv(file: File): Promise<ImportBatch> {
  const { data, error, response } = await importsBatchesCreate({ body: { file } });
  if (!data) {
    // A 403 can be a missing/stale CSRF cookie; re-seed so a retry carries a token without a
    // reload (harmless for an auth 403). Same recovery as the review actions (Codex 2026-05-30).
    if (response?.status === 403) seedCsrf();
    // `response` is optional (undefined on a network error before any response).
    const fallback = response
      ? `Upload failed (HTTP ${response.status}).`
      : "Upload failed: could not reach the server.";
    throw new Error(fieldError(error, "file") ?? fallback);
  }
  return data;
}

export function ImportUpload({
  onUploaded,
}: {
  /** Called with the created batch on a successful (2xx) upload — the page uses it to
   *  refresh the batch list. Fires for a failed-parse batch too (it's a real record). */
  onUploaded?: (batch: ImportBatch) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);

  const mutation = useMutation({
    mutationFn: uploadCsv,
    onSuccess: (batch) => {
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onUploaded?.(batch);
    },
  });

  const batch = mutation.data;

  return (
    <div className="rounded-lg border border-border p-4">
      <form
        className="flex flex-wrap items-center gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (file) mutation.mutate(file);
        }}
      >
        <label className="flex items-center gap-2 text-sm">
          <span className="sr-only">Dragon Shield CSV file</span>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            aria-label="Dragon Shield CSV file"
            disabled={mutation.isPending}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="text-sm file:mr-3 file:rounded-md file:border file:border-border file:bg-background file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-muted disabled:opacity-50"
          />
        </label>
        <Button type="submit" size="sm" disabled={!file || mutation.isPending}>
          {mutation.isPending ? "Importing…" : "Import CSV"}
        </Button>
      </form>

      {mutation.isError ? (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {mutation.error?.message}
        </p>
      ) : null}

      {batch ? <UploadResult batch={batch} /> : null}
    </div>
  );
}

function UploadResult({ batch }: { batch: ImportBatch }) {
  if (batch.status === "failed") {
    return (
      <p role="status" className="mt-3 text-sm text-destructive">
        {batch.original_filename} is not a Dragon Shield export
        {batch.error ? `: ${batch.error}` : "."}
      </p>
    );
  }
  return (
    <div
      role="status"
      className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
    >
      <span className="text-muted-foreground">
        Imported {batch.original_filename}:
      </span>
      <BatchStatusPill status={batch.status ?? "review"} />
      <span className="text-muted-foreground tabular-nums">
        {batch.rows_total} rows · {batch.rows_materialized} materialized ·{" "}
        {batch.rows_needs_review} need review
      </span>
      <Link
        href={`/imports/${batch.id}`}
        className="font-medium underline-offset-4 hover:underline"
      >
        Review →
      </Link>
    </div>
  );
}
