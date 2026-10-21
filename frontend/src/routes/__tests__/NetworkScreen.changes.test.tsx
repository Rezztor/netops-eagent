// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as networkApi from "@/api/networkAutomation";
import type { ChangeDetail, ChangeSummary } from "@/api/networkAutomation";
import { ConfirmProvider, ToastProvider } from "@/components";
import { ChangesPanel, SELF_APPROVAL_COPY } from "../NetworkScreen";

vi.mock("@/api/networkAutomation", async (importOriginal) => ({
  ...(await importOriginal<typeof networkApi>()),
  getChangesAwaitingApproval: vi.fn(),
  getChangeForReview: vi.fn(),
  decideChange: vi.fn(),
}));

vi.mock("@/lib/session", () => ({
  useSession: () => session,
}));

let session = {
  authenticated: true as const,
  user: {
    id: "user-asha",
    username: "asha",
    display_name: "Asha",
    email: "asha@example.com",
    role: "power" as const,
    role_title: "Power",
    must_change_password: false,
  },
  permissions: ["network.approve"],
  preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} },
  csrfToken: "csrf",
  runtime: { runtime_id: "r", is_development: true, is_dockerized: false },
  version: { version: "0", commit_time: "" },
  webuiExtensions: null,
  licence: null,
};

const summary: ChangeSummary = {
  id: "chg1",
  status: "awaiting_approval",
  playbook_name: "ios_interface_desc",
  summary: "set description",
  limit: "lab-ios",
  created_at: 1_725_000_000,
  requested_by: "user-asha",
  approved_by: "",
  command_count: 1,
  render_method: "check_mode",
};

const detail: ChangeDetail = {
  ...summary,
  commands: ["interface GigabitEthernet0/0", "description lab uplink"],
  rollback_commands: [],
  rollback_name: "",
  variables: {},
  commit: "abc",
  approved_hash: "",
  current_hash: "hash1",
  error: "",
};

const getChangesAwaitingApproval = vi.mocked(networkApi.getChangesAwaitingApproval);
const getChangeForReview = vi.mocked(networkApi.getChangeForReview);

async function renderPanel() {
  render(
    <ToastProvider>
      <ConfirmProvider>
        <ChangesPanel />
      </ConfirmProvider>
    </ToastProvider>,
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy());
}

beforeEach(() => {
  session = {
    ...session,
    user: { ...session.user, id: "user-asha", username: "asha", display_name: "Asha" },
  };
  getChangesAwaitingApproval.mockResolvedValue({ ok: true, changes: [summary] });
  getChangeForReview.mockResolvedValue({ ok: true, change: detail });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ChangesPanel self-approval", () => {
  it("shows accountability copy for the requester and leaves Approve enabled", async () => {
    await renderPanel();
    const approve = screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(false);

    await act(async () => {
      fireEvent.click(approve);
    });

    await waitFor(() => {
      expect(screen.getByText(SELF_APPROVAL_COPY)).toBeTruthy();
      expect(screen.getByText(/interface GigabitEthernet0\/0/)).toBeTruthy();
    });
    // Dialog confirm is also labelled Approve; both must stay enabled for the requester.
    for (const button of screen.getAllByRole("button", { name: "Approve" })) {
      expect((button as HTMLButtonElement).disabled).toBe(false);
    }
  });

  it("keeps neutral copy when another user would approve", async () => {
    session = {
      ...session,
      user: { ...session.user, id: "user-ravi", username: "ravi", display_name: "Ravi" },
    };
    await renderPanel();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    });

    await waitFor(() => {
      expect(screen.getByText(/If the devices move afterwards/)).toBeTruthy();
      expect(screen.getByText(/interface GigabitEthernet0\/0/)).toBeTruthy();
    });
    expect(screen.queryByText(SELF_APPROVAL_COPY)).toBeNull();
  });
});

describe("ChangesPanel approval binding", () => {
  it("approves the plan it just showed, by its hash", async () => {
    // The panel listed the change earlier; the plan has moved since.
    getChangeForReview.mockResolvedValue({ ok: true, change: { ...detail, current_hash: "hash2" } });
    vi.mocked(networkApi.decideChange).mockResolvedValue({ ok: true });
    await renderPanel();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    });
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(2));
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "Approve" })[1]!);
    });

    await waitFor(() => expect(networkApi.decideChange).toHaveBeenCalledWith("chg1", "approve", "hash2", ""));
  });

  it("asks a change-high plan for its typed confirmation and sends it", async () => {
    getChangeForReview.mockResolvedValue({ ok: true, change: { ...detail, confirm: "lab-ios" } });
    vi.mocked(networkApi.decideChange).mockResolvedValue({ ok: true });
    await renderPanel();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    });
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/interface GigabitEthernet0\/0/)).toBeTruthy();
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    });
    expect(networkApi.decideChange).not.toHaveBeenCalled(); // nothing typed yet

    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "lab-ios" } });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    });
    await waitFor(() => expect(networkApi.decideChange).toHaveBeenCalledWith("chg1", "approve", "hash1", "lab-ios"));
  });
});
