import { describe, expect, it } from "vitest";

import {
  formatDateTimeUtc,
  formatDayShort,
  formatUsd,
  parseDecimal,
} from "./format";

describe("formatUsd", () => {
  it("formats whole and fractional amounts to 2dp USD", () => {
    expect(formatUsd(42)).toBe("$42.00");
    expect(formatUsd(42.1)).toBe("$42.10");
    expect(formatUsd(1234.5)).toBe("$1,234.50");
    expect(formatUsd(0)).toBe("$0.00");
  });
});

describe("parseDecimal", () => {
  it("parses DRF decimal strings", () => {
    expect(parseDecimal("42.10")).toBe(42.1);
    expect(parseDecimal("0.00")).toBe(0);
  });

  it("returns null for missing/blank/invalid — never coerces to 0", () => {
    expect(parseDecimal(null)).toBeNull();
    expect(parseDecimal(undefined)).toBeNull();
    expect(parseDecimal("")).toBeNull();
    expect(parseDecimal("not-a-number")).toBeNull();
  });
});

describe("formatDayShort", () => {
  it("formats an ISO date as a short month/day label", () => {
    expect(formatDayShort("2026-05-12")).toBe("May 12");
    expect(formatDayShort("2026-01-01")).toBe("Jan 1");
  });

  it("parses in UTC so a date can't drift across midnight in a non-UTC locale", () => {
    // A bare date read in OS-local time could roll to Dec 30 west of UTC; the
    // UTC pin keeps it Dec 31 regardless of the test runner's timezone.
    expect(formatDayShort("2026-12-31")).toBe("Dec 31");
  });
});

describe("formatDateTimeUtc", () => {
  it("formats an ISO datetime as a fixed en-US + UTC label (SSR/CSR-stable)", () => {
    expect(formatDateTimeUtc("2026-06-16T23:45:00Z")).toBe("Jun 16, 23:45 UTC");
  });

  it("renders midnight as 00:05 (hour12:false ICU edge), never 24:05", () => {
    expect(formatDateTimeUtc("2026-06-16T00:05:00Z")).toBe("Jun 16, 00:05 UTC");
  });

  it("renders noon as 12:00", () => {
    expect(formatDateTimeUtc("2026-06-16T12:00:00Z")).toBe("Jun 16, 12:00 UTC");
  });

  it("falls back to the raw string on an unparseable value (never throws)", () => {
    // A non-ISO passthrough (e.g. a malformed Healthchecks last_ping) must not crash
    // the render — Intl.format would throw RangeError on an Invalid Date.
    expect(formatDateTimeUtc("not-a-date")).toBe("not-a-date");
  });
});
