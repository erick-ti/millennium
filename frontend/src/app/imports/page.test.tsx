import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ImportBatch, PaginatedImportBatchList } from "@/lib/api";
import { importsBatchesListOptions } from "@/lib/api";

import ImportsPage from "./page";

// The page + its ImportUpload child are the consumers under test; the typed
// client is mocked so no real fetch fires. `importsBatchesCreate` is mocked for
// the child upload control (unused in these list-focused tests).
vi.mock("@/lib/api", () => ({
  importsBatchesListOptions: vi.fn(),
  importsBatchesListQueryKey: vi.fn(() => [{ _id: "importsBatchesList" }]),
  importsBatchesCreate: vi.fn(),
}));

// The ImportUpload child reads useAuth; mock an owner session so its form renders
// (the demo read-only path is covered in import-upload.test.tsx).
vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: { id: 1, username: "reader", email: "" },
    isLoading: false,
    isAuthenticated: true,
    isDemo: false,
    canWrite: true,
    refetch: vi.fn(),
  }),
}));

const listOptions = vi.mocked(importsBatchesListOptions);

type BatchesPage = PaginatedImportBatchList;

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

function stubBatches(impl: (page: number) => BatchesPage) {
  listOptions.mockImplementation((options) => {
    const page = options?.query?.page ?? 1;
    return {
      queryKey: [{ _id: "importsBatchesList", query: { page } }],
      queryFn: async () => impl(page),
    } as unknown as ReturnType<typeof importsBatchesListOptions>;
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ImportsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ImportsPage", () => {
  it("shows a loading skeleton before data resolves", () => {
    stubBatches(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(
      screen.getByRole("status", { name: /loading imports/i }),
    ).toBeInTheDocument();
  });

  it("renders each batch as a row linking to its detail, with status and counts", async () => {
    stubBatches(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeBatch()],
    }));
    renderPage();

    const link = await screen.findByRole("link", { name: "collection.csv" });
    expect(link).toHaveAttribute("href", "/imports/5");
    // status pill + counts render
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("May 29")).toBeInTheDocument();
    expect(screen.getByText(/1 import/)).toBeInTheDocument();
  });

  it("renders a friendly empty state when there are no imports", async () => {
    stubBatches(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(await screen.findByText(/No imports yet/i)).toBeInTheDocument();
  });

  it("renders a first-load error with retry", async () => {
    stubBatches(() => {
      throw new Error("403");
    });
    renderPage();

    expect(
      await screen.findByText(/Couldn.t load your imports/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("always renders the upload control above the history", () => {
    stubBatches(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(
      screen.getByLabelText(/dragon shield csv file/i),
    ).toBeInTheDocument();
  });

  it("pages forward through the batch history", async () => {
    stubBatches((page) =>
      page === 1
        ? {
            count: 150,
            next: "http://test/?page=2",
            previous: null,
            results: [makeBatch({ id: 1, original_filename: "page-one.csv" })],
          }
        : {
            count: 150,
            next: null,
            previous: "http://test/?page=1",
            results: [makeBatch({ id: 2, original_filename: "page-two.csv" })],
          },
    );
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("page-one.csv")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /next/i }));

    expect(await screen.findByText("page-two.csv")).toBeInTheDocument();
    expect(listOptions).toHaveBeenCalledWith({ query: { page: 2 } });
  });
});
