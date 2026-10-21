// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

let admin = false;
vi.mock("@/lib/session", () => ({ useCan: () => admin }));

import { Transcript } from "../Transcript";

afterEach(() => cleanup());

const items = [
  { no: 0, type: "user", content: "check sw-01" },
  { no: 1, type: "code_exe", heading: "ran show version" },
  { no: 2, type: "error", content: "The code runner is not available.", kvps: { details: "Traceback /srv/app/runner.py" } },
  { no: 3, type: "response", content: "I could not reach the runner." },
];

/** Open the folded group, then the failed step inside it (the last row with its sentence). */
function openFailedStep() {
  fireEvent.click(screen.getByRole("button", { expanded: false, name: /steps/ }));
  const rows = screen.getAllByRole("button", { name: /The code runner is not available/ });
  fireEvent.click(rows[rows.length - 1]!);
}

function show() {
  render(<Transcript items={items} onBranch={() => {}} onAnswer={() => {}} onCompose={() => {}} />);
}

describe("Transcript", () => {
  it("labels who said what and says on the group header that a step failed", () => {
    show();
    expect(screen.getByText("You")).toBeTruthy();
    expect(screen.getByText("Agent")).toBeTruthy();
    expect(screen.getByText(/1 failed/)).toBeTruthy();
  });

  it("shows the traceback to administrators only", () => {
    admin = false;
    show();
    openFailedStep();
    expect(document.body.textContent).not.toContain("/srv/app");
    cleanup();
    admin = true;
    show();
    openFailedStep();
    expect(screen.getByText("Technical details")).toBeTruthy();
    expect(document.body.textContent).toContain("/srv/app");
  });

  it("shows a group of nothing but housekeeping as one folded line", () => {
    render(
      <Transcript
        items={[{ no: 0, type: "util", heading: "Saved what it learned" }]}
        onBranch={() => {}} onAnswer={() => {}} onCompose={() => {}}
      />,
    );
    const header = screen.getByRole("button", { name: /Saved what it learned/ });
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(header.textContent).toContain("1 housekeeping step");
  });
});
