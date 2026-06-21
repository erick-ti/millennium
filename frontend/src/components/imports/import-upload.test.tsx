import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ImportBatch } from "@/lib/api";
import { csrfRetrieve, importsBatchesCreate } from "@/lib/api";

import { ImportUpload } from "./import-upload";

// Only the upload SDK fn is mocked; the status pill, button, and types stay real.
// csrfRetrieve is mocked because the upload re-seeds the CSRF cookie on a 403 (via lib/csrf).
vi.mock("@/lib/api", () => ({
  importsBatchesCreate: vi.fn(),
  csrfRetrieve: vi.fn(async () => ({})),
}));

// Auth state is controllable so the upload form (owner) vs the read-only notice (demo)
// can both be exercised. Owner by default; the demo test flips `auth.canWrite`.
const auth = vi.hoisted(() => ({ canWrite: true }));
vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: { id: 1, username: auth.canWrite ? "reader" : "demo", email: "" },
    isLoading: false,
    isAuthenticated: true,
    isDemo: !auth.canWrite,
    canWrite: auth.canWrite,
    refetch: vi.fn(),
  }),
}));

const createMock = vi.mocked(importsBatchesCreate);
const csrfMock = vi.mocked(csrfRetrieve);

type CreateResult = Awaited<ReturnType<typeof importsBatchesCreate>>;

function makeBatch(overrides: Partial<ImportBatch> = {}): ImportBatch {
  return {
    id: 5,
    source_format: "dragon_shield",
    status: "review",
    original_filename: "collection.csv",
    error: "",
    created_at: "2026-05-29T10:00:00Z",
    updated_at: "2026-05-29T10:00:00Z",
    rows_total: 3,
    rows_materialized: 1,
    rows_skipped: 0,
    rows_pending: 2,
    rows_error: 0,
    rows_needs_review: 2,
    ...overrides,
  };
}

function resolved(value: {
  data?: ImportBatch;
  error?: unknown;
  response?: { status: number };
}): CreateResult {
  return value as unknown as CreateResult;
}

function renderUpload(onUploaded?: (batch: ImportBatch) => void) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ImportUpload onUploaded={onUploaded} />
    </QueryClientProvider>,
  );
}

function csvFile(name = "collection.csv") {
  return new File(['"sep=,"\nheader\nrow\n'], name, { type: "text/csv" });
}

beforeEach(() => {
  vi.clearAllMocks();
  auth.canWrite = true;
});

describe("ImportUpload", () => {
  it("hides the file input and shows a sign-in notice for the read-only demo", () => {
    auth.canWrite = false;
    renderUpload();

    expect(
      screen.queryByRole("button", { name: /import csv/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /sign in/i })).toBeInTheDocument();
  });

  it("keeps the submit button disabled until a file is chosen", async () => {
    renderUpload();
    const submit = screen.getByRole("button", { name: /import csv/i });
    expect(submit).toBeDisabled();

    await userEvent.upload(screen.getByLabelText(/dragon shield csv file/i), csvFile());
    expect(submit).toBeEnabled();
  });

  it("uploads the file and shows the resulting batch summary with a review link", async () => {
    createMock.mockResolvedValue(
      resolved({ data: makeBatch(), response: { status: 201 } }),
    );
    const onUploaded = vi.fn();
    renderUpload(onUploaded);

    await userEvent.upload(screen.getByLabelText(/dragon shield csv file/i), csvFile());
    await userEvent.click(screen.getByRole("button", { name: /import csv/i }));

    expect(await screen.findByText(/Imported collection\.csv/i)).toBeInTheDocument();
    expect(screen.getByText(/2 need review/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /review/i })).toHaveAttribute(
      "href",
      "/imports/5",
    );
    expect(createMock).toHaveBeenCalledWith({ body: { file: expect.any(File) } });
    expect(onUploaded).toHaveBeenCalledTimes(1);
  });

  it("surfaces a failed-parse batch (a non-DS file) distinctly, not as a crash", async () => {
    createMock.mockResolvedValue(
      resolved({
        data: makeBatch({
          status: "failed",
          original_filename: "notes.csv",
          error: "missing required Dragon Shield columns",
          rows_total: 0,
        }),
        response: { status: 201 },
      }),
    );
    renderUpload();

    await userEvent.upload(
      screen.getByLabelText(/dragon shield csv file/i),
      csvFile("notes.csv"),
    );
    await userEvent.click(screen.getByRole("button", { name: /import csv/i }));

    expect(
      await screen.findByText(/notes\.csv is not a Dragon Shield export/i),
    ).toBeInTheDocument();
  });

  it("re-seeds the CSRF cookie when the upload returns 403", async () => {
    createMock.mockResolvedValue(resolved({ response: { status: 403 } }));
    renderUpload();

    await userEvent.upload(screen.getByLabelText(/dragon shield csv file/i), csvFile());
    await userEvent.click(screen.getByRole("button", { name: /import csv/i }));

    expect(await screen.findByText(/Upload failed \(HTTP 403\)/i)).toBeInTheDocument();
    expect(csrfMock).toHaveBeenCalled();
  });

  it("shows a validation error when the server rejects the file", async () => {
    createMock.mockResolvedValue(
      resolved({
        error: { file: ["file exceeds the 10 MB upload limit"] },
        response: { status: 400 },
      }),
    );
    renderUpload();

    await userEvent.upload(screen.getByLabelText(/dragon shield csv file/i), csvFile());
    await userEvent.click(screen.getByRole("button", { name: /import csv/i }));

    expect(
      await screen.findByText(/file exceeds the 10 MB upload limit/i),
    ).toBeInTheDocument();
  });
});
