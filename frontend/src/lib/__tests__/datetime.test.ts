import { describe, expect, it } from "vitest";

import {
  formatDateTime,
  formatRelative,
  formatTime,
  toDate,
  toIso,
  type TimePreferences,
} from "../datetime";

const utc24: TimePreferences = { timezone: "UTC", time_format: "24h" };
const utc12: TimePreferences = { timezone: "UTC", time_format: "12h" };

// A fixed instant: 2026-09-13T14:30:00Z.
const INSTANT = Date.UTC(2026, 8, 13, 14, 30, 0);

describe("toDate", () => {
  it("returns null for empty inputs", () => {
    expect(toDate(null)).toBeNull();
    expect(toDate(undefined)).toBeNull();
    expect(toDate("")).toBeNull();
    expect(toDate("not a date")).toBeNull();
  });

  it("treats small numbers as epoch seconds", () => {
    const seconds = Math.floor(INSTANT / 1000);
    expect(toDate(seconds)?.getTime()).toBe(INSTANT);
    expect(toDate(INSTANT)?.getTime()).toBe(INSTANT);
  });

  it("parses ISO strings and Dates", () => {
    expect(toDate("2026-09-13T14:30:00Z")?.getTime()).toBe(INSTANT);
    expect(toDate(new Date(INSTANT))?.getTime()).toBe(INSTANT);
  });
});

describe("formatting honours the timezone and 12/24h preference", () => {
  it("renders 24h time in the chosen zone", () => {
    expect(formatTime(INSTANT, utc24)).toBe("14:30");
  });

  it("renders 12h time with a meridiem", () => {
    const text = formatTime(INSTANT, utc12);
    expect(text).toMatch(/2:30/);
    expect(text.toLowerCase()).toContain("pm");
  });

  it("includes date and time together", () => {
    expect(formatDateTime(INSTANT, utc24)).toContain("2026");
    expect(formatDateTime(INSTANT, utc24)).toContain("14:30");
  });

  it("falls back to the browser zone for an invalid timezone", () => {
    // Should not throw; just produces some non-empty string.
    const text = formatDateTime(INSTANT, { timezone: "Nowhere/Invalid", time_format: "24h" });
    expect(text.length).toBeGreaterThan(0);
  });
});

describe("toIso and formatRelative", () => {
  it("emits a machine ISO string", () => {
    expect(toIso(INSTANT)).toBe("2026-09-13T14:30:00.000Z");
    expect(toIso(null)).toBe("");
  });

  it("describes near-now instants", () => {
    const now = INSTANT;
    expect(formatRelative(INSTANT, now)).toBe("just now");
    expect(formatRelative(INSTANT - 3 * 60_000, now)).toMatch(/3/);
  });
});
