import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PaginationControls } from "./pagination-controls";

// Regression coverage for the two a11y rules the logic carries: Prev/Next
// must be disabled ONLY at true boundaries, never on isPaging (disabling the
// focused button traps keyboard focus to <body>), and a mid-flight click
// must no-op (double-click guard).

function setup(props: Partial<Parameters<typeof PaginationControls>[0]> = {}) {
  const onPageChange = vi.fn();
  render(
    <PaginationControls
      page={2}
      totalPages={3}
      count={250}
      noun="card"
      isPaging={false}
      hasPrev={true}
      hasNext={true}
      onPageChange={onPageChange}
      {...props}
    />,
  );
  return { onPageChange };
}

describe("PaginationControls", () => {
  it("renders nothing when count is 0", () => {
    const { container } = render(
      <PaginationControls
        page={1}
        totalPages={1}
        count={0}
        noun="card"
        isPaging={false}
        hasPrev={false}
        hasNext={false}
        onPageChange={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("disables Prev/Next only at the true boundaries", () => {
    setup({ page: 1, hasPrev: false, hasNext: true });
    expect(screen.getByRole("button", { name: /prev/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
  });

  it("keeps both buttons ENABLED while paging (no focus trap) and no-ops clicks", async () => {
    const user = userEvent.setup();
    const { onPageChange } = setup({ isPaging: true, hasPrev: true, hasNext: true });

    const next = screen.getByRole("button", { name: /next/i });
    const prev = screen.getByRole("button", { name: /prev/i });
    // Critically NOT disabled mid-flight: disabling the focused button blurs
    // focus to <body>.
    expect(next).toBeEnabled();
    expect(prev).toBeEnabled();

    await user.click(next);
    await user.click(prev);
    // The isPaging guard no-ops both clicks (double-click / mid-flight guard).
    expect(onPageChange).not.toHaveBeenCalled();
  });

  it("navigates by delta when not paging", async () => {
    const user = userEvent.setup();
    const { onPageChange } = setup({ page: 2, isPaging: false });

    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onPageChange).toHaveBeenCalledWith(3);

    await user.click(screen.getByRole("button", { name: /prev/i }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it("pluralizes the noun and renders a live page-status region", () => {
    setup({ page: 2, totalPages: 3, count: 250, noun: "card" });
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/Page 2 of 3/);
    expect(status).toHaveTextContent(/250 cards/);
  });
});
