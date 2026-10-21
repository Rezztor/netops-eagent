// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/api/client";
import { ConfirmProvider } from "@/components";
import { interactionOf } from "@/lib/transcript";
import { InteractionCard } from "../InteractionCard";

vi.mock("@/api/client", () => ({ apiRequest: vi.fn().mockResolvedValue({ ok: true }) }));
let canApprove = true;
vi.mock("@/lib/session", () => ({ useCan: () => canApprove }));

afterEach(() => {
  cleanup();
  canApprove = true;
  vi.mocked(apiRequest).mockClear();
});

const card = (extra: Record<string, unknown> = {}) => ({
  interaction: "decision",
  interaction_id: "i1",
  question: "Change the description on Ethernet0?",
  options: JSON.stringify([{ label: "Approve", tone: "go" }, { label: "Reject", tone: "stop" }]),
  endpoint: "plugins/_ea_ansible/change_approve",
  payloads: JSON.stringify([
    { change_id: "c1", expected_hash: "h1", decision: "approve" },
    { change_id: "c1", expected_hash: "h1", decision: "reject" },
  ]),
  allow_other: "false",
  ...extra,
});

function show(extra: Record<string, unknown> = {}) {
  render(
    <ConfirmProvider>
      <InteractionCard interaction={interactionOf(card(extra))!} live context="" onAnswer={vi.fn()} onCompose={vi.fn()} />
    </ConfirmProvider>,
  );
}

const button = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) }) as HTMLButtonElement;

describe("a decision card for someone without approval rights", () => {
  it("turns both decisions off and says why", () => {
    canApprove = false;
    show();
    expect(button("Approve").disabled).toBe(true);
    expect(button("Reject").disabled).toBe(true);
    expect(screen.getByText(/needs an engineer with approval rights/)).toBeTruthy();
  });
});

describe("a disabled Approve", () => {
  it("says what it is waiting for", () => {
    show({ tier: "change-high", confirm: "lab-sonic" });
    expect(button("Approve").disabled).toBe(true);
    expect(screen.getByText("Approve is off until you type lab-sonic above.")).toBeTruthy();
  });
});

describe("Reject", () => {
  it("asks before it records anything, and Cancel records nothing", async () => {
    show();
    fireEvent.click(button("Reject"));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await Promise.resolve();
    expect(vi.mocked(apiRequest).mock.calls.filter(([e]) => e === "plugins/_ea_ansible/change_approve")).toHaveLength(0);
  });
});
