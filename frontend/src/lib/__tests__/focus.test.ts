import { describe, expect, it } from "vitest";

import { isFocused, withFocus } from "../focus";

describe("focus mode lives in the URL", () => {
  it("reads the flag", () => {
    expect(isFocused("?focus=1")).toBe(true);
    expect(isFocused("")).toBe(false);
    expect(isFocused("?focus=0")).toBe(false);
    expect(isFocused("?other=1")).toBe(false);
  });

  it("turns focus on and off", () => {
    expect(withFocus("", true)).toBe("?focus=1");
    expect(withFocus("?focus=1", false)).toBe("");
  });

  it("keeps every other parameter", () => {
    // The conversation id travels in the URL too; dropping it on expand would
    // open focus mode on the wrong thread.
    expect(withFocus("?ctx=abc123", true)).toBe("?ctx=abc123&focus=1");
    expect(withFocus("?ctx=abc123&focus=1", false)).toBe("?ctx=abc123");
  });

  it("does not add a second copy when already focused", () => {
    expect(withFocus("?focus=1", true)).toBe("?focus=1");
  });
});
