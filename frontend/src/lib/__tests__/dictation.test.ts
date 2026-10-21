import { describe, expect, it } from "vitest";

import { DICTATION_START_LABEL, DICTATION_STOP_LABEL } from "../dictation";

describe("dictation privacy label", () => {
  it("says the browser sends the audio to its maker's speech service", () => {
    // Chrome and Edge stream the microphone to the vendor; the control must say so.
    expect(DICTATION_START_LABEL.toLowerCase()).toContain("browser");
    expect(DICTATION_START_LABEL.toLowerCase()).toContain("audio");
    expect(DICTATION_START_LABEL.toLowerCase()).toMatch(/speech service|maker/);
    expect(DICTATION_STOP_LABEL).toBe("Stop dictating");
  });
});
