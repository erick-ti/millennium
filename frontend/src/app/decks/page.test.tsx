import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Deck, PaginatedDeckList } from "@/lib/api";
import { decksDecksCreate, decksDecksListOptions, csrfRetrieve } from "@/lib/api";

import DecksPage from "./page";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  decksDecksListOptions: vi.fn(),
  decksDecksListQueryKey: vi.fn(() => [{ _id: "decksDecksList" }]),
  decksDecksCreate: vi.fn(),
  // seedCsrf (via lib/csrf) calls this on a write 403; resolve so the fire-and-forget
  // re-seed never throws (the alerts/imports test convention).
  csrfRetrieve: vi.fn(async () => ({})),
}));

const listOptions = vi.mocked(decksDecksListOptions);
const createDeckFn = vi.mocked(decksDecksCreate);
const csrfMock = vi.mocked(csrfRetrieve);

function makeDeck(overrides: Partial<Deck> = {}): Deck {
  return {
    id: 1,
    name: "Snake-Eye",
    description: "Tier 1 build",
    member_count: 3,
    created_at: "2026-05-31T00:00:00Z",
    updated_at: "2026-05-31T00:00:00Z",
    ...overrides,
  };
}

function stubDecks(impl: (page: number) => PaginatedDeckList) {
  listOptions.mockImplementation((options) => {
    const page = options?.query?.page ?? 1;
    return {
      queryKey: [{ _id: "decksDecksList", query: options?.query }],
      queryFn: async () => impl(page),
    } as unknown as ReturnType<typeof decksDecksListOptions>;
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DecksPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  stubDecks(() => ({ count: 0, next: null, previous: null, results: [] }));
  createDeckFn.mockResolvedValue({
    data: makeDeck(),
    error: undefined,
    response: { status: 201 } as Response,
    request: {} as Request,
  });
});

describe("DecksPage — list", () => {
  it("shows a loading skeleton before data resolves", () => {
    renderPage();
    expect(
      screen.getByRole("status", { name: /loading decks/i }),
    ).toBeInTheDocument();
  });

  it("queries page 1 on first load", () => {
    renderPage();
    expect(listOptions).toHaveBeenCalledWith({ query: { page: 1 } });
  });

  it("renders a deck row with a name link and member count", async () => {
    stubDecks(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeDeck({ id: 7, name: "Fire King", member_count: 12 })],
    }));
    renderPage();

    const link = await screen.findByRole("link", { name: "Fire King" });
    expect(link).toHaveAttribute("href", "/decks/7");
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Tier 1 build")).toBeInTheDocument();
  });

  it("renders a friendly empty state", async () => {
    renderPage();
    expect(await screen.findByText(/No decks yet/i)).toBeInTheDocument();
  });

  it("renders a first-load error with retry", async () => {
    stubDecks(() => {
      throw new Error("boom");
    });
    renderPage();
    expect(await screen.findByText(/Couldn.t load your decks/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("pages forward", async () => {
    stubDecks((page) =>
      page === 1
        ? {
            count: 150,
            next: "http://test/?page=2",
            previous: null,
            results: [makeDeck({ id: 1, name: "Deck Page One" })],
          }
        : {
            count: 150,
            next: null,
            previous: "http://test/?page=1",
            results: [makeDeck({ id: 2, name: "Deck Page Two" })],
          },
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: "Deck Page One" });
    await user.click(screen.getByRole("button", { name: /next/i }));

    await screen.findByRole("link", { name: "Deck Page Two" });
    expect(listOptions).toHaveBeenCalledWith({ query: { page: 2 } });
  });
});

describe("DecksPage — create form", () => {
  it("submits the deck with the trimmed name and description", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: /deck name/i }), "  Snake-Eye  ");
    await user.type(screen.getByRole("textbox", { name: /description/i }), "Tier 1");
    await user.click(screen.getByRole("button", { name: /create deck/i }));

    expect(createDeckFn).toHaveBeenCalledWith({
      body: { name: "Snake-Eye", description: "Tier 1" },
    });
    expect(await screen.findByText(/Deck created/i)).toBeInTheDocument();
  });

  it("disables submit until a name is present", async () => {
    const user = userEvent.setup();
    renderPage();

    const submit = screen.getByRole("button", { name: /create deck/i });
    expect(submit).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: /deck name/i }), "x");
    expect(submit).toBeEnabled();
  });

  it("surfaces a 400 field error from the server", async () => {
    createDeckFn.mockResolvedValue({
      data: undefined,
      error: { name: ["must not be blank"] },
      response: { status: 400 } as Response,
      request: {} as Request,
    });
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: /deck name/i }), "x");
    await user.click(screen.getByRole("button", { name: /create deck/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("must not be blank");
  });

  it("re-seeds the CSRF cookie when create returns 403", async () => {
    createDeckFn.mockResolvedValue({
      data: undefined,
      error: undefined,
      response: { status: 403 } as Response,
      request: {} as Request,
    });
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: /deck name/i }), "x");
    await user.click(screen.getByRole("button", { name: /create deck/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/HTTP 403/);
    expect(csrfMock).toHaveBeenCalledTimes(1);
  });
});
