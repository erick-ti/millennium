import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "./empty-state";

describe("EmptyState", () => {
  it("renders the title and optional description", () => {
    render(
      <EmptyState
        title="No holdings yet"
        description="Import a Dragon Shield CSV to populate your collection."
      />,
    );
    expect(screen.getByText("No holdings yet")).toBeInTheDocument();
    expect(
      screen.getByText("Import a Dragon Shield CSV to populate your collection."),
    ).toBeInTheDocument();
  });

  it("renders an action slot when provided", () => {
    render(
      <EmptyState
        title="Nothing here"
        action={<a href="#import">Import a CSV</a>}
      />,
    );
    expect(
      screen.getByRole("link", { name: "Import a CSV" }),
    ).toBeInTheDocument();
  });

  it("hides a decorative icon from assistive tech", () => {
    const { container } = render(
      <EmptyState title="Empty" icon={<svg data-testid="glyph" />} />,
    );
    // The icon wrapper is aria-hidden so the glyph isn't announced.
    expect(container.querySelector('[aria-hidden="true"]')).toBeInTheDocument();
  });
});
