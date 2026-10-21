// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/api/client";
import { interactionOf } from "@/lib/transcript";
import { ConfirmProvider } from "@/components";
import { InteractionCard } from "../InteractionCard";

vi.mock("@/api/client", () => ({ apiRequest: vi.fn() }));
vi.mock("@/lib/session", () => ({ useCan: () => true }));

const kvps = (extra: Record<string, unknown> = {}) => ({
  interaction: "decision",
  interaction_id: "d_1",
  question: "Apply `changes/x` on lab-sonic?",
  options: JSON.stringify([{ label: "Approve", tone: "go" }, { label: "Reject", tone: "stop" }]),
  endpoint: "plugins/_ea_ansible/change_approve",
  payloads: JSON.stringify([
    { change_id: "c1", expected_hash: "h1", decision: "approve" },
    { change_id: "c1", expected_hash: "h1", decision: "reject" },
  ]),
  commands: JSON.stringify(["config vlan add 100"]),
  diff: JSON.stringify(["--- before", "+vlan 100"]),
  rollback: JSON.stringify(["config vlan del 100"]),
  tier: "change-high",
  allow_other: "false",
  multi: "false",
  ...extra,
});

const show = (extra?: Record<string, unknown>) =>
  render(<ConfirmProvider><InteractionCard interaction={interactionOf(kvps(extra))!} live context="" onAnswer={vi.fn()} onCompose={vi.fn()} /></ConfirmProvider>);

afterEach(() => {
  cleanup();
  vi.mocked(apiRequest).mockReset();
});

describe("a change on its card", () => {
  it("shows the diff, the rollback and the tier", () => {
    show();
    expect(screen.getByLabelText("Diff").textContent).toContain("+vlan 100");
    expect(screen.getByLabelText("Rollback").textContent).toBe("config vlan del 100");
    expect(screen.getByText("Change · high risk")).toBeTruthy();
  });

  it("keeps Approve disabled until the confirmation is typed, then sends it with the given payload", async () => {
    vi.mocked(apiRequest).mockResolvedValue({ ok: true });
    show({ confirm: "lab-sonic" });
    const approve = screen.getByText("Approve").closest("button")!;
    expect(approve.disabled).toBe(true);
    expect((screen.getByText("Reject").closest("button") as HTMLButtonElement).disabled).toBe(false);
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "lab-son" } });
    expect(approve.disabled).toBe(true);
    fireEvent.change(box, { target: { value: "lab-sonic" } });
    expect(approve.disabled).toBe(false);
    fireEvent.click(approve);
    await waitFor(() => expect(apiRequest).toHaveBeenCalled());
    expect(apiRequest).toHaveBeenCalledWith("plugins/_ea_ansible/change_approve", {
      body: { change_id: "c1", expected_hash: "h1", decision: "approve", confirmation: "lab-sonic" },
    });
  });

  it("has no confirmation field on a low-tier card, and Reject never carries one", async () => {
    vi.mocked(apiRequest).mockResolvedValue({ ok: true });
    show({ tier: "change-low" });
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(screen.getByText("Reject"));
    // Reject asks first, in the app's own dialog.
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith("plugins/_ea_ansible/change_approve", {
        body: { change_id: "c1", expected_hash: "h1", decision: "reject" },
      }),
    );
  });
});

describe("a device card after a reload", () => {
  it("draws what the server says, so a change that already ran offers no second Approve", async () => {
    vi.mocked(apiRequest).mockResolvedValue({ ok: true, change: { status: "closed", kind: "read" } });
    show();
    await waitFor(() => expect(screen.getByText(/Read finished/)).toBeTruthy());
    expect(apiRequest).toHaveBeenCalledWith("plugins/_ea_ansible/changes_list", { body: { change_id: "c1" } });
    expect(screen.queryByText("Approve")).toBeNull();
  });
});
