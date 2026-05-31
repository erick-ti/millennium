import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { QueryErrorState } from "./query-error-state";

describe("QueryErrorState", () => {
  it("renders the title + default unreachable/session-expired copy and a working Retry", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(<QueryErrorState title="Couldn't load X." onRetry={onRetry} />);

    expect(screen.getByText("Couldn't load X.")).toBeInTheDocument();
    expect(screen.getByText(/session expired/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("omits the paged-escape button when no onBack/backLabel is given", () => {
    render(<QueryErrorState title="Couldn't load X." onRetry={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: /back to page/i }),
    ).not.toBeInTheDocument();
  });

  it("renders the paged-escape button when provided and wires onBack", async () => {
    const user = userEvent.setup();
    const onBack = vi.fn();
    render(
      <QueryErrorState
        title="Couldn't load X."
        onRetry={vi.fn()}
        backLabel="Back to page 1"
        onBack={onBack}
      />,
    );

    await user.click(screen.getByRole("button", { name: /back to page 1/i }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("accepts a custom description", () => {
    render(
      <QueryErrorState
        title="Nope."
        description="Something specific happened."
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText("Something specific happened.")).toBeInTheDocument();
    expect(screen.queryByText(/session expired/i)).not.toBeInTheDocument();
  });
});
