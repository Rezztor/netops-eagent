// @vitest-environment jsdom
/**
 * Settings, Network automation: approving from the queue reads the plan again, asks a change-high plan for its typed
 * confirmation and sends it with the hash. Without it change_approve refuses the approval.
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as networkApi from "@/api/networkAutomation";
import type { ChangeDetail, ChangeSummary } from "@/api/networkAutomation";
import { ConfirmProvider } from "@/components";
import { ApprovalQueue } from "../NetworkAutomationSection";

vi.mock("@/api/networkAutomation", async (importOriginal) => ({
  ...(await importOriginal<typeof networkApi>()),
  getChangesAwaitingApproval: vi.fn(),
  getChangeForReview: vi.fn(),
  decideChange: vi.fn(),
}));

const summary: ChangeSummary = {
  id: "chg1",
  status: "awaiting_approval",
  playbook_name: "changes/ios_vlan_name",
  summary: "name VLAN 300",
  limit: "sw1",
  created_at: 1_725_000_000,
  requested_by: "user-asha",
  approved_by: "",
  command_count: 2,
  render_method: "reviewed_cli",
};

const detail: ChangeDetail = {
  ...summary,
  commands: ["vlan 300", "name users"],
  rollback_commands: [],
  rollback_name: "",
  variables: {},
  commit: "abc",
  approved_hash: "",
  current_hash: "hash1",
  error: "",
};

async function openPlan() {
  render(
    <ConfirmProvider>
      <ApprovalQueue />
    </ConfirmProvider>,
  );
  const read = await screen.findByRole("button", { name: "Read the commands" });
  await act(async () => {
    fireEvent.click(read);
  });
  const approve = await screen.findByRole("button", { name: "Approve these commands" });
  await act(async () => {
    fireEvent.click(approve);
  });
  return screen.findByRole("dialog");
}

beforeEach(() => {
  vi.mocked(networkApi.getChangesAwaitingApproval).mockResolvedValue({ ok: true, changes: [summary] });
  vi.mocked(networkApi.decideChange).mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ApprovalQueue", () => {
  it("asks a change-high plan for its typed confirmation and sends it with the hash", async () => {
    vi.mocked(networkApi.getChangeForReview).mockResolvedValue({ ok: true, change: { ...detail, confirm: "sw1" } });
    const dialog = await openPlan();
    expect(within(dialog).getByText(/vlan 300/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    });
    expect(networkApi.decideChange).not.toHaveBeenCalled(); // nothing typed yet

    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "sw1" } });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    });
    await waitFor(() => expect(networkApi.decideChange).toHaveBeenCalledWith("chg1", "approve", "hash1", "sw1"));
  });

  it("approves a plan without a tier after a plain confirm, and not at all when cancelled", async () => {
    vi.mocked(networkApi.getChangeForReview).mockResolvedValue({ ok: true, change: detail });
    let dialog = await openPlan();
    expect(within(dialog).queryByRole("textbox")).toBeNull();
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    });
    expect(networkApi.decideChange).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Approve these commands" }));
    });
    dialog = await screen.findByRole("dialog");
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    });
    await waitFor(() => expect(networkApi.decideChange).toHaveBeenCalledWith("chg1", "approve", "hash1", ""));
  });
});
