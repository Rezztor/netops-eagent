import { describe, expect, it } from "vitest";

import { attachmentOf, readableSize } from "@/api/attachments";

describe("reading an attachment off a log entry", () => {
  it("reads a complete one", () => {
    expect(
      attachmentOf({
        attachment_id: "a".repeat(32),
        attachment_name: "report.xlsx",
        attachment_bytes: "2048",
      }),
    ).toEqual({ id: "a".repeat(32), name: "report.xlsx", bytes: 2048 });
  });

  it("ignores an entry that carries no attachment", () => {
    expect(attachmentOf(null)).toBeNull();
    expect(attachmentOf({})).toBeNull();
    expect(attachmentOf({ reasoning: "unrelated" })).toBeNull();
  });

  it("refuses a half-populated one rather than rendering a broken control", () => {
    expect(attachmentOf({ attachment_id: "x" })).toBeNull();
    expect(attachmentOf({ attachment_name: "report.xlsx" })).toBeNull();
  });

  it("survives a size that is missing or nonsense", () => {
    const base = { attachment_id: "b".repeat(32), attachment_name: "f.txt" };
    expect(attachmentOf(base)?.bytes).toBe(0);
    expect(attachmentOf({ ...base, attachment_bytes: "not a number" })?.bytes).toBe(0);
  });
});

describe("sizes", () => {
  it("reads at a glance", () => {
    expect(readableSize(512)).toBe("512 B");
    expect(readableSize(2048)).toBe("2 KB");
    expect(readableSize(5_242_880)).toBe("5.0 MB");
  });
});
