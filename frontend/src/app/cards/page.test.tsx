import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CardList, PaginatedCardListList } from "@/lib/api";
import { cardsCardsListOptions } from "@/lib/api";

import CardsPage from "./page";

// next/link needs the App Router context to render; stub it to a plain anchor so
// href assertions are deterministic without mounting a router.
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

// Only the one read-option factory the page calls is stubbed; types still
// resolve from the real module (vi.mock is runtime-only).
vi.mock("@/lib/api", () => ({
  cardsCardsListOptions: vi.fn(),
}));

const cardsOptions = vi.mocked(cardsCardsListOptions);

type CardsPage = PaginatedCardListList;

function makeCard(overrides: Partial<CardList> = {}): CardList {
  return {
    id: 1,
    passcode: 14558127,
    name: "Ash Blossom & Joyous Spring",
    printings_count: 2,
    ...overrides,
  };
}

function stubCards(impl: (page: number) => CardsPage) {
  cardsOptions.mockImplementation((options) => {
    const page = options?.query?.page ?? 1;
    return {
      queryKey: [{ _id: "cardsCardsList", query: { page } }],
      queryFn: async () => impl(page),
    } as unknown as ReturnType<typeof cardsCardsListOptions>;
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CardsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CardsPage", () => {
  it("shows a loading skeleton before data resolves", () => {
    stubCards(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(
      screen.getByRole("status", { name: /loading cards/i }),
    ).toBeInTheDocument();
  });

  it("renders one row per card with a name link and printing count", async () => {
    stubCards(() => ({
      count: 2,
      next: null,
      previous: null,
      results: [
        makeCard({ id: 1, name: "Ash Blossom & Joyous Spring", printings_count: 2 }),
        makeCard({
          id: 5,
          name: "Dark Magician",
          passcode: 46986414,
          printings_count: 7,
        }),
      ],
    }));
    renderPage();

    const darkMagician = await screen.findByRole("link", {
      name: "Dark Magician",
    });
    expect(darkMagician).toHaveAttribute("href", "/cards/5");
    expect(
      screen.getByRole("link", { name: "Ash Blossom & Joyous Spring" }),
    ).toHaveAttribute("href", "/cards/1");
    // printing-count cells
    expect(screen.getByRole("cell", { name: "2" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "7" })).toBeInTheDocument();
    expect(screen.getByText(/Page 1 of 1/)).toBeInTheDocument();
    expect(screen.getByText(/2 cards/)).toBeInTheDocument();
  });

  it("renders a friendly empty state and no pagination footer", async () => {
    stubCards(() => ({ count: 0, next: null, previous: null, results: [] }));
    renderPage();

    expect(await screen.findByText(/No cards yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();
  });

  it("renders a first-load error with retry and no stranding back-control", async () => {
    stubCards(() => {
      throw new Error("403");
    });
    renderPage();

    expect(
      await screen.findByText(/Couldn.t load the card catalog/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /back to page/i }),
    ).not.toBeInTheDocument();
  });

  it("pages forward, requesting the next page and flipping boundary states", async () => {
    stubCards((page) =>
      page === 1
        ? {
            count: 150,
            next: "http://test/?page=2",
            previous: null,
            results: [makeCard({ id: 1, name: "Card Page One" })],
          }
        : {
            count: 150,
            next: null,
            previous: "http://test/?page=1",
            results: [makeCard({ id: 2, name: "Card Page Two" })],
          },
    );
    const user = userEvent.setup();
    renderPage();

    expect(
      await screen.findByRole("link", { name: "Card Page One" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /prev/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /next/i }));

    expect(
      await screen.findByRole("link", { name: "Card Page Two" }),
    ).toBeInTheDocument();
    expect(cardsOptions).toHaveBeenCalledWith({ query: { page: 2 } });
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /prev/i })).toBeEnabled();
  });
});
