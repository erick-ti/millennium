import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CountUp } from "./count-up";

describe("CountUp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the formatted final value immediately under reduced motion (SSR-safe contract)", () => {
    // jsdom has no matchMedia; stub it to report prefers-reduced-motion so the
    // effect early-returns and the seeded value is never reset to 0. Pins the
    // docstring contract: a no-JS / reduced-motion / not-yet-scrolled visitor
    // sees the REAL, comma-formatted number — not a count-up from zero. Guards
    // the realistic "fix the animation by seeding state at 0" regression.
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }));

    render(<CountUp value={14388} />);
    expect(screen.getByText("14,388")).toBeInTheDocument();
  });
});
