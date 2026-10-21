import { describe, expect, it } from "vitest";

import { toCsv } from "../ActivityScreen";

describe("the activity export", () => {
  it("writes a value that starts like a formula as text", () => {
    // A spreadsheet runs a cell that starts with = + - or @. A tool's name comes from whatever server offers
    // the tool, and the file is opened by an administrator reviewing what happened.
    const csv = toCsv([
      { ts: "2026-10-11T10:00:00Z", event: "tool.call", tool: '=HYPERLINK("http://x.example","open")', outcome: "ok" },
      { ts: "2026-10-11T10:00:01Z", event: "@SUM(1)", tool: "+1", duration_ms: 12, outcome: "-2" },
    ] as never);
    const [, first, second] = csv.split("\n");
    expect(first).toContain(`"'=HYPERLINK(""http://x.example"",""open"")"`);
    expect(second).toBe(`"2026-10-11T10:00:01Z","'@SUM(1)","","","'+1","12","'-2"`);
  });
});
