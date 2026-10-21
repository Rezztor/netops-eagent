// @vitest-environment jsdom
/**
 * The Approvals page: opens the change a notification linked to, asks a change-high approval for its typed
 * confirmation and sends it, and leaves an Azure change to its chat card.
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as networkApi from "@/api/networkAutomation";
import type { ApprovalWaiting, ChangeDetail } from "@/api/networkAutomation";
import { ConfirmProvider, ToastProvider } from "@/components";
import { ApprovalsScreen } from "../ApprovalsScreen";

vi.mock("@/api/networkAutomation", async (importOriginal) => ({
  ...(await importOriginal<typeof networkApi>()),
  getApprovalsWaiting: vi.fn(),
  getChangeForReview: vi.fn(),
  decideChange: vi.fn(),
}));

vi.mock("@/lib/session", () => ({
  useSession: () => ({
    user: { id: "user-ravi", username: "ravi", display_name: "Ravi" },
    permissions: ["network.approve"],
    preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} },
  }),
}));

const high: ApprovalWaiting = {
  id: "chg1",
  kind: "change",
  status: "awaiting_approval",
  playbook_name: "changes/ios_vlan_name",
  summary: "name VLAN 300",
  limit: "sw1",
  created_at: 1_725_000_000,
  expires_at: 1_725_001_800,
  requested_by: "user-asha",
  requested_by_name: "Asha",
  approved_by: "",
  command_count: 1,
  render_method: "reviewed_cli",
  tier: "change-high",
  confirm: "sw1",
  azure: false,
  context_id: "",
};

const azure: ApprovalWaiting = {
  ...high,
  id: "az1",
  kind: "azure_change",
  summary: "add a tag",
  tier: "",
  confirm: "",
  azure: true,
  requested_by: "user-ravi",
  requested_by_name: "Ravi",
  context_id: "ctx1",
};

const detail = {
  ...high,
  commands: ["vlan 300", "name users"],
  rollback_commands: [],
  rollback_name: "",
  variables: {},
  commit: "abc",
  approved_hash: "",
  current_hash: "hash1",
  error: "",
} as ChangeDetail;

function renderAt(url: string) {
  render(
    <MemoryRouter initialEntries={[url]}>
      <ToastProvider>
        <ConfirmProvider>
          <ApprovalsScreen />
        </ConfirmProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(networkApi.getApprovalsWaiting).mockResolvedValue([high, azure]);
  vi.mocked(networkApi.getChangeForReview).mockResolvedValue({ ok: true, change: detail });
  vi.mocked(networkApi.decideChange).mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ApprovalsScreen", () => {
  it("opens the change the notification linked to and sends an Azure change to its card", async () => {
    renderAt("/approvals?change=chg1");
    expect(await screen.findByTestId("approval-plan")).toBeTruthy();
    expect(networkApi.getChangeForReview).toHaveBeenCalledWith("chg1");
    expect(screen.getByText("2 waiting for a signature")).toBeTruthy();

    const azureRow = screen.getByTestId("approval-az1");
    expect(within(azureRow).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(within(azureRow).getByRole("link", { name: "Open the card" }).getAttribute("href")).toBe("/chat?ctx=ctx1");
  });

  it("asks a change-high approval for its confirmation and sends it with the hash", async () => {
    renderAt("/approvals");
    const row = await screen.findByTestId("approval-chg1");
    await act(async () => {
      fireEvent.click(within(row).getByRole("button", { name: "Approve" }));
    });
    const input = await screen.findByRole("textbox");
    fireEvent.change(input, { target: { value: "sw1" } });
    const dialog = screen.getByRole("dialog");
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    });
    await waitFor(() => expect(networkApi.decideChange).toHaveBeenCalledWith("chg1", "approve", "hash1", "sw1"));
  });
});
