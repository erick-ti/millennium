import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ImportBatch,
  ImportRow,
  PaginatedImportRowList,
} from "@/lib/api";
import {
  cardsCardsListOptions,
  cardsPrintingsListOptions,
  csrfRetrieve,
  importsBatchesRetrieveOptions,
  importsRowsApproveCreate,
  importsRowsListOptions,
  importsRowsOverrideCreate,
  importsRowsRejectCreate,
} from "@/lib/api";

import { ImportBatchDetail } from "./import-batch-detail";

vi.mock("@/lib/api", () => ({
  importsBatchesRetrieveOptions: vi.fn(),
  importsBatchesRetrieveQueryKey: vi.fn(() => [{ _id: "importsBatchesRetrieve" }]),
  importsRowsListOptions: vi.fn(),
  importsRowsListQueryKey: vi.fn(() => [{ _id: "importsRowsList" }]),
  importsRowsApproveCreate: vi.fn(),
  importsRowsRejectCreate: vi.fn(),
  importsRowsOverrideCreate: vi.fn(),
  cardsCardsListOptions: vi.fn(),
  cardsPrintingsListOptions: vi.fn(),
  // Re-seed-on-403 calls this via lib/csrf; resolve so the fire-and-forget never throws.
  csrfRetrieve: vi.fn(async () => ({})),
}));

const batchOptions = vi.mocked(importsBatchesRetrieveOptions);
const rowsOptions = vi.mocked(importsRowsListOptions);
const approveMock = vi.mocked(importsRowsApproveCreate);
const rejectMock = vi.mocked(importsRowsRejectCreate);
const overrideMock = vi.mocked(importsRowsOverrideCreate);
const cardsOptions = vi.mocked(cardsCardsListOptions);
const printingsOptions = vi.mocked(cardsPrintingsListOptions);
const csrfMock = vi.mocked(csrfRetrieve);

function makeBatch(overrides: Partial<ImportBatch> = {}): ImportBatch {
  return {
    id: 5,
    source_format: "dragon_shield",
    status: "review",
    original_filename: "collection.csv",
    error: "",
    created_at: "2026-05-29T10:00:00Z",
    updated_at: "2026-05-29T10:00:00Z",
    rows_total: 1,
    rows_materialized: 0,
    rows_skipped: 0,
    rows_pending: 1,
    rows_error: 0,
    rows_needs_review: 1,
    ...overrides,
  };
}

function makeRow(overrides: Partial<ImportRow> = {}): ImportRow {
  return {
    id: 10,
    batch: 5,
    row_number: 1,
    raw_data: {},
    normalized_data: {
      card_name: "Ash Blossom & Joyous Spring",
      set_code: "L5DD-ENC09",
      set_rarity: "Common",
    },
    matched_printing: {
      id: 100,
      card_name: "Ash Blossom & Joyous Spring",
      set_code: "L5DD-ENC09",
      set_rarity: "Common",
      variant_label: null,
      is_multi_variant: false,
    },
    match_confidence: "medium",
    status: "pending",
    error_message: "",
    needs_review: true,
    created_at: "2026-05-29T10:00:00Z",
    updated_at: "2026-05-29T10:00:00Z",
    ...overrides,
  };
}

type RowsPage = PaginatedImportRowList;
type ApproveResult = Awaited<ReturnType<typeof importsRowsApproveCreate>>;

function asResult(value: {
  data?: ImportRow;
  response?: { status: number };
}): ApproveResult {
  return value as unknown as ApproveResult;
}

function stubRows(
  impl: (query: { page: number; status?: string; needs_review?: boolean }) => RowsPage,
) {
  rowsOptions.mockImplementation((options) => {
    const query = options?.query ?? {};
    return {
      queryKey: [{ _id: "importsRowsList", query }],
      queryFn: async () =>
        impl({
          page: query.page ?? 1,
          status: query.status,
          needs_review: query.needs_review,
        }),
    } as unknown as ReturnType<typeof importsRowsListOptions>;
  });
}

function onePage(rows: ImportRow[]): RowsPage {
  return { count: rows.length, next: null, previous: null, results: rows };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderDetail() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ImportBatchDetail batchId={5} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  batchOptions.mockReturnValue({
    queryKey: [{ _id: "importsBatchesRetrieve" }],
    queryFn: async () => makeBatch(),
  } as unknown as ReturnType<typeof importsBatchesRetrieveOptions>);
  // The picker's queries default to empty/disabled; stub so it renders if opened.
  cardsOptions.mockReturnValue({
    queryKey: [{ _id: "cardsCardsList" }],
    queryFn: async () => ({ count: 0, next: null, previous: null, results: [] }),
  } as unknown as ReturnType<typeof cardsCardsListOptions>);
  printingsOptions.mockReturnValue({
    queryKey: [{ _id: "cardsPrintingsList" }],
    queryFn: async () => ({ count: 0, next: null, previous: null, results: [] }),
  } as unknown as ReturnType<typeof cardsPrintingsListOptions>);
});

describe("ImportBatchDetail", () => {
  it("renders the batch header and one row per staged row, with action buttons", async () => {
    stubRows(() => onePage([makeRow()]));
    renderDetail();

    expect(await screen.findByRole("heading", { name: "collection.csv" })).toBeInTheDocument();
    expect(screen.getByText("Ash Blossom & Joyous Spring")).toBeInTheDocument();
    expect(screen.getByText("Medium")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /reject/i })).toBeInTheDocument();
  });

  it("approves a row and shows a success message", async () => {
    stubRows(() => onePage([makeRow()]));
    approveMock.mockResolvedValue(
      asResult({ data: makeRow({ status: "materialized" }), response: { status: 200 } }),
    );
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));

    expect(await screen.findByText(/Approved and added to your collection/i)).toBeInTheDocument();
    expect(approveMock).toHaveBeenCalledWith({ path: { id: 10 } });
  });

  it("surfaces the 409 changed-duplicate conflict distinctly (row stays pending)", async () => {
    stubRows(() => onePage([makeRow()]));
    approveMock.mockResolvedValue(asResult({ response: { status: 409 } }));
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));

    expect(
      await screen.findByText(/different quantity or cost/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/cost basis isn't overwritten/i)).toBeInTheDocument();
  });

  it("re-seeds the CSRF cookie when an action returns 403 (recoverable without reload)", async () => {
    stubRows(() => onePage([makeRow()]));
    approveMock.mockResolvedValue(asResult({ response: { status: 403 } }));
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));

    // failure() re-seeds on 403 (before the error surfaces) so the next attempt carries a token.
    expect(await screen.findByText(/Approve failed \(HTTP 403\)/i)).toBeInTheDocument();
    expect(csrfMock).toHaveBeenCalled();
  });

  it("rejects a row and shows the skipped message", async () => {
    stubRows(() => onePage([makeRow()]));
    rejectMock.mockResolvedValue(
      asResult({ data: makeRow({ status: "skipped" }), response: { status: 200 } }),
    );
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    await userEvent.click(screen.getByRole("button", { name: /reject/i }));

    expect(await screen.findByText(/rejected and skipped/i)).toBeInTheDocument();
    expect(rejectMock).toHaveBeenCalledWith({ path: { id: 10 } });
  });

  it("disables Approve for an unmatched row and falls back to the normalized card name", async () => {
    stubRows(() =>
      onePage([
        makeRow({
          matched_printing: null,
          match_confidence: "unmatched",
          normalized_data: {
            card_name: "Unmatched Card",
            set_code: "ZZZ-001",
            set_rarity: "Common",
          },
        }),
      ]),
    );
    renderDetail();

    expect(await screen.findByText("Unmatched Card")).toBeInTheDocument();
    expect(screen.getByText("Unmatched")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeDisabled();
    // override is still available to pick a printing
    expect(screen.getByRole("button", { name: /override/i })).toBeEnabled();
  });

  it("marks a multi-variant placeholder match", async () => {
    stubRows(() =>
      onePage([
        makeRow({
          matched_printing: {
            id: 100,
            card_name: "Ash Blossom & Joyous Spring",
            set_code: "L5DD-ENC09",
            set_rarity: "Common",
            variant_label: null,
            is_multi_variant: true,
          },
        }),
      ]),
    );
    renderDetail();

    expect(await screen.findByText(/multi-variant/i)).toBeInTheDocument();
  });

  it("opens the override picker and can cancel it", async () => {
    stubRows(() => onePage([makeRow()]));
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    await userEvent.click(screen.getByRole("button", { name: /override/i }));

    expect(
      await screen.findByText(/Choose the correct printing/i),
    ).toBeInTheDocument();
    expect(overrideMock).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText(/Choose the correct printing/i)).not.toBeInTheDocument();
  });

  it("overrides a row: search → pick card → pick printing fires the override mutation with the right shape", async () => {
    stubRows(() => onePage([makeRow()]));
    // Non-empty card search + a printing for the picker to drill into.
    cardsOptions.mockImplementation((options) => {
      const search = options?.query?.search ?? "";
      return {
        queryKey: [{ _id: "cardsCardsList", search }],
        queryFn: async () => ({
          count: search ? 1 : 0,
          next: null,
          previous: null,
          results: search
            ? [{ id: 1, passcode: 14558127, name: "Ash Blossom & Joyous Spring", printings_count: 1 }]
            : [],
        }),
      } as unknown as ReturnType<typeof cardsCardsListOptions>;
    });
    printingsOptions.mockReturnValue({
      queryKey: [{ _id: "cardsPrintingsList" }],
      queryFn: async () => ({
        count: 1,
        next: null,
        previous: null,
        results: [
          {
            id: 200,
            card: 1,
            card_name: "Ash Blossom & Joyous Spring",
            set_code: "L5DD-ENC09",
            set_rarity: "Common",
            variant_label: null,
            set_name: "Legendary 5D's Decks",
            is_multi_variant: false,
          },
        ],
      }),
    } as unknown as ReturnType<typeof cardsPrintingsListOptions>);
    overrideMock.mockResolvedValue({
      data: makeRow(),
      response: { status: 200 },
    } as unknown as Awaited<ReturnType<typeof importsRowsOverrideCreate>>);
    const user = userEvent.setup();
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    await user.click(screen.getByRole("button", { name: /override/i }));
    await user.type(screen.getByLabelText(/search a card by name/i), "Ash");
    // The card result is a button (the row's card cell is a plain cell, not a button).
    await user.click(await screen.findByRole("button", { name: /Ash Blossom & Joyous Spring/i }));
    await user.click(await screen.findByRole("button", { name: /L5DD-ENC09/i }));

    expect(overrideMock).toHaveBeenCalledWith({ path: { id: 10 }, body: { printing: 200 } });
    expect(await screen.findByText(/Match updated/i)).toBeInTheDocument();
  });

  it("filters rows by the Show selector", async () => {
    stubRows((query) =>
      query.needs_review
        ? onePage([
            makeRow({
              id: 10,
              matched_printing: null,
              normalized_data: { card_name: "Needs Review Card" },
            }),
          ])
        : onePage([
            makeRow({ id: 10 }),
            makeRow({
              id: 11,
              status: "materialized",
              matched_printing: null,
              normalized_data: { card_name: "Done Card" },
            }),
          ]),
    );
    const user = userEvent.setup();
    renderDetail();

    expect(await screen.findByText("Ash Blossom & Joyous Spring")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/filter rows/i), "needs_review");

    expect(await screen.findByText("Needs Review Card")).toBeInTheDocument();
    expect(rowsOptions).toHaveBeenLastCalledWith({
      query: { batch: 5, page: 1, needs_review: true },
    });
  });

  it("renders a dash instead of actions for a non-pending row", async () => {
    stubRows(() =>
      onePage([makeRow({ status: "materialized", needs_review: false })]),
    );
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("closes the override picker when the filter changes", async () => {
    stubRows(() => onePage([makeRow()]));
    const user = userEvent.setup();
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    await user.click(screen.getByRole("button", { name: /override/i }));
    expect(await screen.findByText(/Choose the correct printing/i)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/filter rows/i), "needs_review");

    expect(screen.queryByText(/Choose the correct printing/i)).not.toBeInTheDocument();
  });

  it("disables row actions while a page transition is showing stale rows", async () => {
    const page2 = deferred<RowsPage>();
    rowsOptions.mockImplementation((options) => {
      const query = options?.query ?? {};
      const page = query.page ?? 1;
      return {
        queryKey: [{ _id: "importsRowsList", query }],
        queryFn: () =>
          page === 1
            ? Promise.resolve({
                count: 150,
                next: "http://test/?page=2",
                previous: null,
                results: [makeRow()],
              })
            : page2.promise,
      } as unknown as ReturnType<typeof importsRowsListOptions>;
    });
    const user = userEvent.setup();
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: /next/i }));

    // Page 2 is in flight; keepPreviousData still shows page-1 rows, but their actions must be
    // inert so a mid-transition click can't approve/skip a row that's about to scroll away.
    expect(screen.getByRole("button", { name: /approve/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /reject/i })).toBeDisabled();

    page2.resolve({
      count: 150,
      next: null,
      previous: "http://test/?page=1",
      results: [
        makeRow({ id: 11, matched_printing: null, normalized_data: { card_name: "Page Two Card" } }),
      ],
    });
    expect(await screen.findByText("Page Two Card")).toBeInTheDocument();
  });
});
