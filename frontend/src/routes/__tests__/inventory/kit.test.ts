import { describe, expect, it } from "vitest";

import type { DeviceComputed, LifecycleDate } from "@/api/inventory";

import { lifecycleSummary, sourceLabel } from "../../inventory/kit";

const none: LifecycleDate = { date: null, days: null, state: "none" };
const computed = (life: Partial<DeviceComputed["lifecycle"]>): DeviceComputed => ({
  access_method: "ssh",
  monitored: false,
  completeness: { percent: 100, missing: [] },
  lifecycle: { warranty: none, support: none, eol: none, ...life },
  lifecycle_state: "none",
});

describe("lifecycleSummary", () => {
  it("names the worst fact first: expired, then ending soon, then covered", () => {
    expect(lifecycleSummary(undefined)).toEqual({ text: "No dates", tone: "none" });
    expect(lifecycleSummary(computed({}))).toEqual({ text: "No dates", tone: "none" });
    expect(
      lifecycleSummary(
        computed({
          warranty: { date: "2026-11-09", days: 38, state: "soon" },
          support: { date: "2026-09-20", days: -12, state: "expired" },
        }),
      ),
    ).toEqual({ text: "Support expired 12 days ago", tone: "crit" });
    expect(lifecycleSummary(computed({ warranty: { date: "2026-10-03", days: 1, state: "soon" } }))).toEqual({
      text: "Warranty ends in 1 day",
      tone: "warn",
    });
    expect(lifecycleSummary(computed({ eol: { date: "2026-11-09", days: 38, state: "soon" } })).text).toBe(
      "End of life in 38 days",
    );
    expect(
      lifecycleSummary(
        computed({
          warranty: { date: "2028-03-31", days: 546, state: "ok" },
          eol: { date: "2029-10-31", days: 1125, state: "ok" },
        }),
      ),
    ).toEqual({ text: "Covered to Mar 2028", tone: "none" });
  });
});

describe("sourceLabel", () => {
  it("is empty for a person and names a feed", () => {
    const at = { approved_by: "a", approved_at: "", observed_at: null };
    expect(sourceLabel(undefined)).toBe("");
    expect(sourceLabel({ source: "manual", ...at })).toBe("");
    expect(sourceLabel({ source: "extraction", ...at })).toBe("");
    expect(sourceLabel({ source: "facts", ...at })).toBe("device");
    expect(sourceLabel({ source: "solarwinds", ...at })).toBe("solarwinds");
  });
});
