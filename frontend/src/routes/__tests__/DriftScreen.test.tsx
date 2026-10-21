// @vitest-environment jsdom
/** The drift inbox: the counts in the header, unexplained marked, and the redacted diff of the two backups. */

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as backupsApi from "@/api/inventoryBackups";
import { ToastProvider } from "@/components";
import { DriftScreen } from "../DriftScreen";

vi.mock("@/api/inventoryBackups", async (importOriginal) => ({
  ...(await importOriginal<typeof backupsApi>()),
  getDrift: vi.fn(),
  diffBackup: vi.fn(),
}));

vi.mock("@/lib/session", () => {
  const session = {
    permissions: ["inventory.view", "inventory.manage"],
    preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} },
  };
  return { useSession: () => session, useCan: (...required: string[]) => required.every((p) => session.permissions.includes(p)) };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DriftScreen", () => {
  it("counts, marks unexplained and opens the diff of the two backups", async () => {
    vi.mocked(backupsApi.getDrift).mockResolvedValue({
      ok: true,
      changed: 2,
      unexplained: 1,
      items: [
        { device_id: "d2", name: "sw2", from: "c", to: "d", since: "2026-10-18T13:40:00Z", changed_at: "2026-10-18T15:20:00Z", explained: false, changes: [] },
        {
          device_id: "d1", name: "sw1", from: "a", to: "b", since: "2026-10-18T13:00:00Z", changed_at: "2026-10-18T14:00:00Z", explained: true,
          changes: [{ id: "c1", summary: "name VLAN 300", status: "applied", approved_by: "Asha", applied_at: "2026-10-18T13:30:00+00:00" }],
        },
      ],
    });
    vi.mocked(backupsApi.diffBackup).mockResolvedValue({ ok: true, diff: "-vlan 1\n+vlan 2", changed_lines: 2 });
    render(
      <MemoryRouter>
        <ToastProvider>
          <DriftScreen />
        </ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("2 devices changed, 1 unexplained")).toBeTruthy();
    const sw2 = screen.getByTestId("drift-sw2");
    expect(within(sw2).getByText("unexplained")).toBeTruthy();
    expect(within(sw2).getByRole("link").getAttribute("href")).toBe("/inventory?device=d2");
    expect(within(screen.getByTestId("drift-sw1")).getByText(/name VLAN 300, approved by Asha/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(within(sw2).getByRole("button", { name: "View diff" }));
    });
    expect(backupsApi.diffBackup).toHaveBeenCalledWith("d2", "c", "d");
    expect((await screen.findByTestId("drift-diff")).textContent).toContain("+vlan 2");
  });

  it("is a setup step when network automation is not configured, and keeps Retry for a real failure", async () => {
    const draw = () =>
      render(
        <MemoryRouter>
          <ToastProvider>
            <DriftScreen />
          </ToastProvider>
        </MemoryRouter>,
      );
    vi.mocked(backupsApi.getDrift).mockResolvedValue({ ok: false, error: "Network automation is not configured on this instance. An administrator must set the Ansible executor address." });
    draw();
    expect(await screen.findByText("Network automation is not set up yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    cleanup();

    vi.mocked(backupsApi.getDrift).mockResolvedValue({ ok: false, error: "The executor could not be reached, so backups are unknown." });
    draw();
    expect(await screen.findByText("Could not work out what changed")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});
