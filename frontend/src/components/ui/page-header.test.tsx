import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "./page-header";

describe("PageHeader", () => {
  it("renders the kicker, the title as the page h1, and an optional subtitle", () => {
    render(
      <PageHeader
        kicker="LEDGER"
        title="Collection"
        subtitle="Your holdings, valued daily."
      />,
    );

    expect(screen.getByText("LEDGER")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Collection" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Your holdings, valued daily.")).toBeInTheDocument();
  });

  it("omits the subtitle when none is given", () => {
    render(<PageHeader kicker="CATALOG" title="Cards" />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Cards" }),
    ).toBeInTheDocument();
  });

  it("renders an actions slot when provided", () => {
    render(
      <PageHeader
        kicker="THE WATCH"
        title="Movers"
        actions={<button type="button">Window</button>}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Window" }),
    ).toBeInTheDocument();
  });
});
