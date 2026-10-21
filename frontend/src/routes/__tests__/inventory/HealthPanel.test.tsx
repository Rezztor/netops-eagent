// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as inventoryApi from "@/api/inventory";
import * as backupsApi from "@/api/inventoryBackups";
import * as reportsApi from "@/api/inventoryReports";
import { ToastProvider } from "@/components";
import { HealthPanel } from "../../inventory/HealthPanel";

vi.mock("@/api/inventoryReports", () => ({
  getLifecycle: vi.fn(),
  getQuality: vi.fn(),
  getRacks: vi.fn(),
}));

vi.mock("@/api/inventoryBackups", () => ({ getBackupReport: vi.fn(), runBackup: vi.fn() }));

vi.mock("@/api/inventory", async (importOriginal) => ({
  ...(await importOriginal<typeof inventoryApi>()),
  listStale: vi.fn(),
  proposeDecommissionStale: vi.fn(),
}));

const m = {
  getLifecycle: vi.mocked(reportsApi.getLifecycle),
  getQuality: vi.mocked(reportsApi.getQuality),
  getBackupReport: vi.mocked(backupsApi.getBackupReport),
  runBackup: vi.mocked(backupsApi.runBackup),
  listStale: vi.mocked(inventoryApi.listStale),
  proposeDecommissionStale: vi.mocked(inventoryApi.proposeDecommissionStale),
};

const counts = {
  warranty: { expired: 1, soon: 2, ok: 3, missing: 4 },
  support: { expired: 0, soon: 1, ok: 5, missing: 4 },
  eol: { expired: 0, soon: 0, ok: 8, missing: 4 },
};

const onOpenDevice = vi.fn();

function renderHealth(url = "/inventory?tab=health") {
  render(
    <MemoryRouter initialEntries={[url]}>
      <ToastProvider>
        <HealthPanel canManage onOpenDevice={onOpenDevice} />
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  m.getLifecycle.mockResolvedValue({
    ok: true,
    today: "2026-10-02",
    within_days: 90,
    counts,
    items: [
      {
        device_id: "d1",
        name: "core-sw",
        site: "HQ",
        vendor: "Cisco",
        model: "9300",
        kind: "warranty",
        date: "2026-09-01",
        days: -31,
        state: "expired",
      },
    ],
  });
  m.getQuality.mockResolvedValue({
    ok: true,
    required: ["serial", "place_id"],
    total: 10,
    complete: 5,
    average: 91,
    by_field: [
      { field: "serial", missing: 3 },
      { field: "place_id", missing: 2 },
    ],
    devices: [
      {
        device_id: "d2",
        name: "edge-1",
        site: "DC",
        percent: 50,
        missing: ["place_id"],
      },
    ],
  });
  m.listStale.mockResolvedValue({
    ok: true,
    stale_days: 7,
    devices: [
      {
        id: "dev-9",
        name: "old-sw",
        status: "offline",
        last_seen_at: null,
        days_unseen: 9,
        sources: ["solarwinds"],
      },
    ],
  });
  m.proposeDecommissionStale.mockResolvedValue({ ok: true, created: 1, batch_id: "b-1", rejected: [] });
  m.getBackupReport.mockResolvedValue({
    ok: true,
    stale_days: 7,
    items: [
      { device_id: "d1", name: "core-sw", state: "stale", taken_at: "2026-09-20T00:00:00Z", age_days: 12 },
      { device_id: "d2", name: "edge-1", state: "none", taken_at: null, age_days: null },
    ],
  });
  m.runBackup.mockResolvedValue({ ok: true, results: [{ device_id: "d2", name: "edge-1", ok: true, id: "b1", changed: true }] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("HealthPanel", () => {
  it("loads lifecycle rows, quality bars and stale propose", async () => {
    renderHealth();
    await waitFor(() => expect(screen.getByTestId("health-panel")).toBeTruthy());
    expect(screen.getByTestId("lifecycle-row-d1-warranty").getAttribute("data-expired")).toBe("true");
    expect(screen.getByTestId("quality-field-serial").textContent).toContain("3 of 10");
    expect(screen.getByTestId("quality-field-place_id").textContent).toContain("Place (to room level)");
    expect(screen.getByText("91%")).toBeTruthy();
    fireEvent.click(screen.getByTestId("lifecycle-open-d1"));
    expect(onOpenDevice).toHaveBeenCalledWith("d1");
    fireEvent.click(screen.getByTestId("quality-open-d2"));
    expect(onOpenDevice).toHaveBeenCalledWith("d2");
    expect(screen.getByTestId("stale-row-dev-9")).toBeTruthy();
    fireEvent.click(screen.getByTestId("stale-propose-dev-9"));
    await waitFor(() =>
      expect(m.proposeDecommissionStale).toHaveBeenCalledWith({ device_ids: ["dev-9"] }),
    );
  });

  it("sends within_days when the window switch changes", async () => {
    renderHealth();
    await waitFor(() => expect(m.getLifecycle).toHaveBeenCalled());
    m.getLifecycle.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "30 days" }));
    await waitFor(() =>
      expect(m.getLifecycle).toHaveBeenCalledWith(expect.objectContaining({ within_days: 30 })),
    );
  });

  it("shows lifecycle and quality errors", async () => {
    m.getLifecycle.mockRejectedValue(new Error("boom"));
    m.getQuality.mockRejectedValue(new Error("qf"));
    renderHealth();
    await waitFor(() => expect(screen.getByText("Could not load lifecycle")).toBeTruthy());
    expect(screen.getByText("boom")).toBeTruthy();
    expect(screen.getByText("Could not load data quality")).toBeTruthy();
  });

  it("hides propose decommission without canManage", async () => {
    render(
      <MemoryRouter initialEntries={["/inventory?tab=health"]}>
        <ToastProvider>
          <HealthPanel canManage={false} onOpenDevice={onOpenDevice} />
        </ToastProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("stale-row-dev-9")).toBeTruthy());
    expect(screen.queryByTestId("stale-propose-dev-9")).toBeNull();
  });

  it("lists devices without a recent backup and backs one up", async () => {
    renderHealth();
    const panel = await screen.findByTestId("backup-panel");
    await waitFor(() => expect(screen.getByTestId("backup-row-d1")).toBeTruthy());
    expect(screen.getByTestId("backup-row-d1").textContent).toContain("Last backup 12 days ago");
    expect(screen.getByTestId("backup-row-d2").textContent).toContain("never backed up");
    expect(panel).toBeTruthy();
    fireEvent.click(screen.getByTestId("backup-now-d2"));
    await waitFor(() => expect(m.runBackup).toHaveBeenCalledWith(["d2"]));
  });
});
