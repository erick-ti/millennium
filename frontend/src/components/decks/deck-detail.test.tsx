import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  CollectionItemList,
  Deck,
  DeckMembership,
  PaginatedCollectionItemListList,
  PaginatedDeckMembershipList,
} from "@/lib/api";
import {
  collectionItemsListOptions,
  csrfRetrieve,
  decksDecksDestroy,
  decksDecksRetrieveOptions,
  decksMembershipsCreate,
  decksMembershipsDestroy,
  decksMembershipsListOptions,
} from "@/lib/api";

import { DeckDetail } from "./deck-detail";

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: nav.push }) }));

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
  decksDecksRetrieveOptions: vi.fn(),
  decksDecksRetrieveQueryKey: vi.fn(() => [{ _id: "decksDecksRetrieve" }]),
  decksDecksListQueryKey: vi.fn(() => [{ _id: "decksDecksList" }]),
  decksDecksDestroy: vi.fn(),
  decksMembershipsListOptions: vi.fn(),
  decksMembershipsListQueryKey: vi.fn(() => [{ _id: "decksMembershipsList" }]),
  decksMembershipsCreate: vi.fn(),
  decksMembershipsDestroy: vi.fn(),
  collectionItemsListOptions: vi.fn(),
  csrfRetrieve: vi.fn(async () => ({})),
}));

const retrieveOptions = vi.mocked(decksDecksRetrieveOptions);
const membersOptions = vi.mocked(decksMembershipsListOptions);
const holdingsOptions = vi.mocked(collectionItemsListOptions);
const createMembershipMock = vi.mocked(decksMembershipsCreate);
const destroyMembershipMock = vi.mocked(decksMembershipsDestroy);
const deleteDeckMock = vi.mocked(decksDecksDestroy);
const csrfMock = vi.mocked(csrfRetrieve);

function makeDeck(overrides: Partial<Deck> = {}): Deck {
  return {
    id: 1,
    name: "Snake-Eye",
    description: "Tier 1 build",
    member_count: 0,
    created_at: "2026-05-31T00:00:00Z",
    updated_at: "2026-05-31T00:00:00Z",
    ...overrides,
  };
}

function makeMembership(overrides: Partial<DeckMembership> = {}): DeckMembership {
  return {
    id: 50,
    deck: 1,
    collection_item: 100,
    quantity: 1,
    card_name: "Ash Blossom & Joyous Spring",
    set_code: "L5DD-ENC09",
    set_rarity: "Common",
    variant_label: null,
    condition: "near_mint",
    edition: "first",
    language: "en",
    portfolio_name: "Yubel Deck",
    created_at: "2026-05-31T00:00:00Z",
    ...overrides,
  };
}

function makeHolding(overrides: Partial<CollectionItemList> = {}): CollectionItemList {
  return {
    id: 100,
    portfolio: 1,
    portfolio_name: "Yubel Deck",
    printing: 9,
    card_name: "Ash Blossom & Joyous Spring",
    set_code: "L5DD-ENC09",
    set_rarity: "Common",
    variant_label: null,
    condition: "near_mint",
    edition: "first",
    language: "en",
    storage_location: null,
    storage_location_name: null,
    quantity: 3,
    ...overrides,
  };
}

function stubDeck(deck: Deck) {
  retrieveOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "decksDecksRetrieve" }],
        queryFn: async () => deck,
      }) as unknown as ReturnType<typeof decksDecksRetrieveOptions>,
  );
}

function stubDeckPending() {
  retrieveOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "decksDecksRetrieve" }],
        queryFn: () => new Promise<Deck>(() => {}),
      }) as unknown as ReturnType<typeof decksDecksRetrieveOptions>,
  );
}

function stubDeckError() {
  retrieveOptions.mockImplementation(
    () =>
      ({
        queryKey: [{ _id: "decksDecksRetrieve" }],
        queryFn: async () => {
          throw new Error("boom");
        },
      }) as unknown as ReturnType<typeof decksDecksRetrieveOptions>,
  );
}

function stubMembers(impl: (page: number) => PaginatedDeckMembershipList) {
  membersOptions.mockImplementation((options) => {
    const page = options?.query?.page ?? 1;
    return {
      queryKey: [{ _id: "decksMembershipsList", query: options?.query }],
      queryFn: async () => impl(page),
    } as unknown as ReturnType<typeof decksMembershipsListOptions>;
  });
}

function stubHoldingSearch(results: CollectionItemList[]) {
  holdingsOptions.mockImplementation((options) => {
    const search = options?.query?.search ?? "";
    const matched: PaginatedCollectionItemListList = {
      count: search ? results.length : 0,
      next: null,
      previous: null,
      results: search ? results : [],
    };
    return {
      queryKey: [{ _id: "collectionItemsList", search }],
      queryFn: async () => matched,
    } as unknown as ReturnType<typeof collectionItemsListOptions>;
  });
}

function renderDetail(client?: QueryClient) {
  const queryClient =
    client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DeckDetail deckId={1} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  stubDeck(makeDeck());
  stubMembers(() => ({ count: 0, next: null, previous: null, results: [] }));
  stubHoldingSearch([makeHolding()]);
  createMembershipMock.mockResolvedValue({
    data: makeMembership(),
    error: undefined,
    response: { status: 201 } as Response,
    request: {} as Request,
  });
  destroyMembershipMock.mockResolvedValue({
    data: undefined,
    error: undefined,
    response: { status: 204 } as Response,
    request: {} as Request,
  });
  deleteDeckMock.mockResolvedValue({
    data: undefined,
    error: undefined,
    response: { status: 204 } as Response,
    request: {} as Request,
  });
});

describe("DeckDetail", () => {
  it("shows a deck loading skeleton while the header resolves", () => {
    stubDeckPending();
    renderDetail();
    expect(
      screen.getByRole("status", { name: /loading deck/i }),
    ).toBeInTheDocument();
  });

  it("renders the deck header (holdings count) and members (per-row copy count)", async () => {
    // 1 distinct holding, but that holding is 3 physical copies — the header counts holdings,
    // the row shows the copies (the Codex 2026-05-31 fix: don't conflate the two).
    stubDeck(makeDeck({ name: "Fire King", description: "Casual", member_count: 1 }));
    stubMembers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeMembership({ quantity: 3 })],
    }));
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "Fire King" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Casual · 1 holding/)).toBeInTheDocument();
    expect(screen.getByText("Ash Blossom & Joyous Spring")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // Copies column
    expect(screen.getByText("Near Mint")).toBeInTheDocument();
    expect(screen.getByText("1st")).toBeInTheDocument();
  });

  it("renders a header-load error with retry", async () => {
    stubDeckError();
    renderDetail();
    expect(await screen.findByText(/Couldn.t load this deck/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("renders an empty members state", async () => {
    renderDetail();
    expect(
      await screen.findByText(/No holdings in this deck yet/i),
    ).toBeInTheDocument();
  });

  it("adds a holding: open picker → search → pick fires the create with the right shape", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: /add holdings/i }));
    await user.type(
      screen.getByLabelText(/search your collection/i),
      "Ash",
    );
    // findBy polls past the 300ms debounce; the result button carries the card name.
    await user.click(await screen.findByRole("button", { name: /Ash Blossom/i }));

    await waitFor(() =>
      expect(createMembershipMock).toHaveBeenCalledWith({
        body: { deck: 1, collection_item: 100 },
      }),
    );
    expect(await screen.findByText(/Added .*to the deck/i)).toBeInTheDocument();
  });

  it("surfaces a 409 when the holding is already in the deck", async () => {
    createMembershipMock.mockResolvedValue({
      data: undefined,
      error: { detail: "This holding is already in the deck." },
      response: { status: 409 } as Response,
      request: {} as Request,
    });
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: /add holdings/i }));
    await user.type(screen.getByLabelText(/search your collection/i), "Ash");
    await user.click(await screen.findByRole("button", { name: /Ash Blossom/i }));

    expect(
      await screen.findByText(/already in this deck/i),
    ).toBeInTheDocument();
  });

  it("re-seeds CSRF when an add returns 403", async () => {
    createMembershipMock.mockResolvedValue({
      data: undefined,
      // 403 with no usable detail body → `failure()` falls back to "HTTP 403"; `{}` matches
      // hey-api's error-branch type (the success branch needs non-undefined `data`).
      error: {},
      response: { status: 403 } as Response,
      request: {} as Request,
    });
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: /add holdings/i }));
    await user.type(screen.getByLabelText(/search your collection/i), "Ash");
    await user.click(await screen.findByRole("button", { name: /Ash Blossom/i }));

    await waitFor(() => expect(csrfMock).toHaveBeenCalled());
  });

  it("removes a member", async () => {
    stubDeck(makeDeck({ member_count: 1 }));
    stubMembers(() => ({
      count: 1,
      next: null,
      previous: null,
      results: [makeMembership({ id: 50 })],
    }));
    const user = userEvent.setup();
    renderDetail();

    await screen.findByText("Ash Blossom & Joyous Spring");
    await user.click(screen.getByRole("button", { name: /remove/i }));

    await waitFor(() =>
      expect(destroyMembershipMock).toHaveBeenCalledWith({ path: { id: 50 } }),
    );
    expect(await screen.findByText(/Removed from the deck/i)).toBeInTheDocument();
  });

  it("deletes the deck after a confirm and navigates to /decks", async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("heading", { name: "Snake-Eye" });
    await user.click(screen.getByRole("button", { name: /delete deck/i }));
    // The confirm step appears; clicking it fires the destroy.
    await user.click(screen.getByRole("button", { name: /confirm delete/i }));

    await waitFor(() =>
      expect(deleteDeckMock).toHaveBeenCalledWith({ path: { id: 1 } }),
    );
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/decks"));
  });

  it("invalidates the /decks list (member_count) after adding a member", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const user = userEvent.setup();
    renderDetail(queryClient);

    await user.click(await screen.findByRole("button", { name: /add holdings/i }));
    await user.type(screen.getByLabelText(/search your collection/i), "Ash");
    await user.click(await screen.findByRole("button", { name: /Ash Blossom/i }));

    // The list row's member_count is a separate cache from the member feed + the deck header.
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: [{ _id: "decksDecksList" }],
      }),
    );
  });

  it("drops the deleted deck's own caches before navigating (so browser-back can't show it)", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const removeSpy = vi.spyOn(queryClient, "removeQueries");
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const user = userEvent.setup();
    renderDetail(queryClient);

    await screen.findByRole("heading", { name: "Snake-Eye" });
    await user.click(screen.getByRole("button", { name: /delete deck/i }));
    await user.click(screen.getByRole("button", { name: /confirm delete/i }));

    await waitFor(() =>
      expect(removeSpy).toHaveBeenCalledWith({
        queryKey: [{ _id: "decksDecksRetrieve" }],
      }),
    );
    expect(removeSpy).toHaveBeenCalledWith({
      queryKey: [{ _id: "decksMembershipsList" }],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: [{ _id: "decksDecksList" }],
    });
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/decks"));
  });
});
