import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cardsCardsListOptions,
  cardsPrintingsListOptions,
} from "@/lib/api";

import { PrintingPicker } from "./printing-picker";

vi.mock("@/lib/api", () => ({
  cardsCardsListOptions: vi.fn(),
  cardsPrintingsListOptions: vi.fn(),
}));

const cardsOptions = vi.mocked(cardsCardsListOptions);
const printingsOptions = vi.mocked(cardsPrintingsListOptions);

beforeEach(() => {
  vi.clearAllMocks();
  cardsOptions.mockImplementation((options) => {
    const search = options?.query?.search ?? "";
    return {
      queryKey: [{ _id: "cardsCardsList", search }],
      queryFn: async () => ({
        count: search ? 1 : 0,
        next: null,
        previous: null,
        results: search
          ? [{ id: 1, passcode: 14558127, name: "Ash Blossom & Joyous Spring", printings_count: 2 }]
          : [],
      }),
    } as unknown as ReturnType<typeof cardsCardsListOptions>;
  });
  printingsOptions.mockImplementation((options) => {
    const card = options?.query?.card;
    return {
      queryKey: [{ _id: "cardsPrintingsList", card }],
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
    } as unknown as ReturnType<typeof cardsPrintingsListOptions>;
  });
});

function renderPicker(props: {
  onSelect?: (id: number) => void;
  onCancel?: () => void;
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PrintingPicker
        onSelect={props.onSelect ?? vi.fn()}
        onCancel={props.onCancel ?? vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe("PrintingPicker", () => {
  it("prompts for at least two characters before searching", () => {
    renderPicker();
    expect(screen.getByText(/Type at least 2 characters/i)).toBeInTheDocument();
  });

  it("searches by name, then lists a card's printings, and returns the chosen id", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderPicker({ onSelect });

    await user.type(screen.getByLabelText(/search a card by name/i), "Ash");

    const cardButton = await screen.findByRole("button", {
      name: /Ash Blossom & Joyous Spring/i,
    });
    await user.click(cardButton);

    const printingButton = await screen.findByRole("button", { name: /L5DD-ENC09/i });
    await user.click(printingButton);

    expect(onSelect).toHaveBeenCalledWith(200);
  });

  it("calls onCancel from the Cancel button", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    renderPicker({ onCancel });

    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
