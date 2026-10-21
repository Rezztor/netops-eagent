// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as inventoryApi from "@/api/inventory";
import type { InventorySource } from "@/api/inventory";
import * as chatApi from "@/api/chat";
import * as summaryApi from "@/api/inventorySummary";
import { ConfirmProvider, ToastProvider } from "@/components";
import { InventoryScreen } from "../../InventoryScreen";

vi.mock("@/api/inventory", async (importOriginal) => ({
  ...(await importOriginal<typeof inventoryApi>()),
  listSources: vi.fn(),
  listBatches: vi.fn(),
  syncSolarwinds: vi.fn(),
  refreshFacts: vi.fn(),
}));
vi.mock("@/api/chat", () => ({ createChat: vi.fn(), sendMessageWithAttachments: vi.fn() }));
vi.mock("@/api/inventorySummary", () => ({ getInventorySummary: vi.fn() }));

// The other tabs are other workers' screens; here they are markers that show what they were given.
vi.mock("../../inventory/DevicesPanel", () => ({
  DevicesPanel: (p: { onOpenRecord: (id: string) => void; onAdd: () => void }) => (
    <div>
      devices-panel
      <button onClick={() => p.onOpenRecord("dev-9")}>open-record</button>
      <button onClick={p.onAdd}>add-here</button>
    </div>
  ),
}));
vi.mock("../../inventory/DeviceRecord", () => ({
  DeviceRecord: (p: { deviceId: string; onEdit: () => void; onBack: () => void }) => (
    <div>
      record-{p.deviceId}
      <button onClick={p.onEdit}>edit-here</button>
      <button onClick={p.onBack}>back-here</button>
    </div>
  ),
}));
vi.mock("../../inventory/DeviceEdit", () => ({
  DeviceEdit: (p: { deviceId: string | null }) => <div>edit-{p.deviceId ?? "new"}</div>,
}));
vi.mock("../../inventory/ReviewPanel", () => ({ ReviewPanel: () => <div>review-panel</div> }));
vi.mock("../../inventory/HealthPanel", () => ({ HealthPanel: () => <div>health-panel</div> }));
vi.mock("../../inventory/PlacesPanel", () => ({ PlacesPanel: () => <div>places-panel</div> }));

vi.mock("@/lib/session", () => ({ useSession: () => session }));

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
  permissions: ["inventory.view", "inventory.manage", "inventory.approve"],
  preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} },
  csrfToken: "csrf",
  runtime: { runtime_id: "r", is_development: true, is_dockerized: false },
  version: { version: "0", commit_time: "" },
  webuiExtensions: null,
  licence: null,
};

const getSummary = vi.mocked(summaryApi.getInventorySummary);
const listSources = vi.mocked(inventoryApi.listSources);
const listBatches = vi.mocked(inventoryApi.listBatches);
const syncSolarwinds = vi.mocked(inventoryApi.syncSolarwinds);
const refreshFacts = vi.mocked(inventoryApi.refreshFacts);

const readySource: InventorySource = {
  source: "solarwinds",
  enabled: true,
  configured: true,
  last_run: null,
  nodes_seen: 0,
  stale_count: 0,
};
const disabledSource: InventorySource = { ...readySource, enabled: false, configured: false };

const zero = { fetched: 0, create: 0, update: 0, unchanged: 0, skipped: 0, needs_review: 0, stale: 0 };

function Where() {
  const { search } = useLocation();
  return <output data-testid="where">{search}</output>;
}

function renderScreen(url = "/inventory") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <ToastProvider>
        <ConfirmProvider>
          <InventoryScreen />
          <Where />
        </ConfirmProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

const where = () => screen.getByTestId("where").textContent;

beforeEach(() => {
  session = { ...session, permissions: ["inventory.view", "inventory.manage", "inventory.approve"] };
  getSummary.mockResolvedValue({
    ok: true,
    devices: 10,
    sites: 3,
    pending: 4,
    conflicts: 2,
    lifecycle: { expired: 1, soon: 2 },
    incomplete: 5,
    average: 91,
    stale: { count: 1, names: ["br-jkt-rtr-01"] },
  });
  listSources.mockResolvedValue({ ok: true, sources: [disabledSource] });
  listBatches.mockResolvedValue({ ok: true, batches: [], total: 0 });
  syncSolarwinds.mockResolvedValue({
    ok: true,
    run_id: "run-1",
    status: "dry_run",
    counts: { ...zero, fetched: 3, create: 1, update: 1, unchanged: 1, needs_review: 1 },
    batch_id: null,
    needs_review: [
      { node_id: "9", observed_name: "dup-a", device_id: "dev-1", device_name: "lab-ios", reason: "hostname-only match" },
    ],
    missing_columns: ["SerialNumber"],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("InventoryScreen shell", () => {
  it("shows five tabs with counts, the header line and four tiles", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByRole("tab", { name: "Review 6" })).toBeTruthy());
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Devices 10",
      "Review 6",
      "Health 3",
      "Places",
      "Sources",
    ]);
    expect(screen.getByText("10 devices across 3 sites")).toBeTruthy();
    expect(screen.getByText("devices-panel")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Waiting for your review/ }).textContent).toMatch(/6/);
    expect(screen.getByText("1 expired · 2 ending soon")).toBeTruthy();
    expect(screen.getByText("Records are 91% complete on average")).toBeTruthy();
    expect(screen.getByText("br-jkt-rtr-01")).toBeTruthy();
  });

  it("a tile jumps to its tab", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /Lifecycle alerts/ }));
    expect(where()).toBe("?tab=health");
    expect(screen.getByText("health-panel")).toBeTruthy();
  });

  it.each([
    ["pending", "review-panel", "?tab=review"],
    ["conflicts", "review-panel", "?tab=review&queue=conflicts"],
    ["quality", "health-panel", "?tab=health"],
    ["racks", "places-panel", "?tab=places"],
  ])("opens the old %s tab in its new place", async (old, marker, search) => {
    renderScreen(`/inventory?tab=${old}`);
    await waitFor(() => expect(screen.getByText(marker)).toBeTruthy());
    await waitFor(() => expect(where()).toBe(search));
  });

  it("routes device, edit and new to the record and form pages", async () => {
    renderScreen();
    fireEvent.click(await screen.findByText("open-record"));
    expect(screen.getByText("record-dev-9")).toBeTruthy();
    expect(where()).toBe("?device=dev-9");
    fireEvent.click(screen.getByText("edit-here"));
    expect(screen.getByText("edit-dev-9")).toBeTruthy();
    expect(where()).toBe("?device=dev-9&edit=1");
  });

  it("Add device opens the empty form; Back from a record returns to the list", async () => {
    renderScreen("/inventory?device=dev-1");
    expect(screen.getByText("record-dev-1")).toBeTruthy();
    fireEvent.click(screen.getByText("back-here"));
    await waitFor(() => expect(screen.getByText("devices-panel")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Add device" }));
    expect(screen.getByText("edit-new")).toBeTruthy();
    expect(where()).toBe("?new=1");
  });

  it("Import goes to Sources", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    expect(where()).toBe("?tab=sources");
  });

  it("hides Import, Add device and edit pages without inventory.manage", async () => {
    session = { ...session, permissions: ["inventory.view"] };
    renderScreen("/inventory?device=dev-1&edit=1");
    expect(screen.getByText("record-dev-1")).toBeTruthy();
    expect(screen.queryByText("edit-dev-1")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add device" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Import" })).toBeNull();
  });

  it("refuses without inventory.view", () => {
    session = { ...session, permissions: [] };
    renderScreen();
    expect(screen.getByText("You don't have access to this page")).toBeTruthy();
    expect(document.body.textContent).not.toContain("inventory.view");
  });
});

async function openSources() {
  renderScreen("/inventory?tab=sources");
  await waitFor(() => expect(screen.getByTestId("sources-panel")).toBeTruthy());
}

describe("Sources tab", () => {
  it("Files card: upload, template and recent imports", async () => {
    listBatches.mockResolvedValue({
      ok: true,
      batches: [
        {
          batch_id: "b1",
          file: "switches-hq.xlsx",
          source: "extraction",
          requested_by: "asha",
          requested_at: "2026-01-04T00:00:00Z",
          counts: { pending: 4, approved: 0, rejected: 0, superseded: 0 },
          total: 4,
        },
      ],
      total: 1,
    });
    await openSources();
    const card = screen.getByTestId("import-from-file");
    expect(within(card).getByText("switches-hq.xlsx")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Upload a file" })).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Download template" })).toBeTruthy();
    expect(screen.getByText(/never overwritten/)).toBeTruthy();
  });

  it("Upload a file opens a new chat with the file attached and goes there", async () => {
    vi.mocked(chatApi.createChat).mockResolvedValue({ context: "ctx-7" });
    vi.mocked(chatApi.sendMessageWithAttachments).mockResolvedValue({ message: "ok", context: "ctx-7" });
    await openSources();
    const file = new File(["name\nsw1"], "switches.csv", { type: "text/csv" });
    await act(async () => {
      fireEvent.change(screen.getByTestId("import-file-input"), { target: { files: [file] } });
    });
    await waitFor(() => expect(where()).toBe("?ctx=ctx-7"));
    expect(chatApi.sendMessageWithAttachments).toHaveBeenCalledWith(
      expect.stringContaining("switches.csv"),
      "ctx-7",
      [file],
    );
  });

  it("shows Disabled chip and help, hides run buttons when SolarWinds is disabled", async () => {
    await openSources();
    expect(screen.getByTestId("source-status-solarwinds").textContent).toBe("Disabled");
    expect(screen.getByTestId("source-disabled-help-solarwinds")).toBeTruthy();
    expect(screen.queryByTestId("solarwinds-dry-run")).toBeNull();
    expect(screen.queryByTestId("solarwinds-run")).toBeNull();
  });

  it("shows Ready chip and Preview / Run buttons when enabled, configured, and approve held", async () => {
    listSources.mockResolvedValue({ ok: true, sources: [readySource] });
    await openSources();
    expect(screen.getByTestId("source-status-solarwinds").textContent).toBe("Ready");
    expect(screen.getByTestId("solarwinds-dry-run")).toBeTruthy();
    expect(screen.getByTestId("solarwinds-run")).toBeTruthy();
  });

  it("hides Preview / Run without inventory.approve", async () => {
    session = { ...session, permissions: ["inventory.view"] };
    listSources.mockResolvedValue({ ok: true, sources: [readySource] });
    await openSources();
    expect(screen.queryByTestId("solarwinds-dry-run")).toBeNull();
    expect(screen.queryByTestId("solarwinds-run")).toBeNull();
  });

  it("Preview posts dry_run true and shows needs_review + missing_columns", async () => {
    listSources.mockResolvedValue({ ok: true, sources: [readySource] });
    await openSources();
    await act(async () => {
      fireEvent.click(screen.getByTestId("solarwinds-dry-run"));
    });
    expect(syncSolarwinds).toHaveBeenCalledWith(true);
    await waitFor(() => expect(screen.getByTestId("needs-review-list")).toBeTruthy());
    expect(screen.getByTestId("needs-review-list").textContent).toMatch(/dup-a/);
    expect(screen.getByTestId("needs-review-list").textContent).toMatch(/lab-ios/);
    expect(screen.getByTestId("missing-columns").textContent).toMatch(/SerialNumber/);
  });

  it("Run and stage posts dry_run false and links the batch in Review", async () => {
    listSources.mockResolvedValue({ ok: true, sources: [readySource] });
    syncSolarwinds.mockResolvedValue({
      ok: true,
      run_id: "run-2",
      status: "succeeded",
      counts: { ...zero, fetched: 2, create: 2 },
      batch_id: "batch-sw-1",
      needs_review: [],
      missing_columns: [],
    });
    await openSources();
    await act(async () => {
      fireEvent.click(screen.getByTestId("solarwinds-run"));
    });
    expect(syncSolarwinds).toHaveBeenCalledWith(false);
    await waitFor(() => expect(screen.getByTestId("sync-batch-link")).toBeTruthy());
    expect(screen.getByTestId("sync-batch-link").getAttribute("href")).toBe("/inventory?tab=review&batch=batch-sw-1");
  });

  it("shows Not configured and Last run failed chips", async () => {
    listSources.mockResolvedValue({ ok: true, sources: [{ ...readySource, configured: false }] });
    await openSources();
    expect(screen.getByTestId("source-status-solarwinds").textContent).toBe("Not configured");
    cleanup();

    listSources.mockResolvedValue({
      ok: true,
      sources: [
        {
          ...readySource,
          last_run: {
            id: "run-f",
            status: "failed",
            started_at: "2026-01-01T00:00:00Z",
            finished_at: "2026-01-01T00:01:00Z",
            counts: zero,
            error: "boom",
            batch_id: null,
          },
        },
      ],
    });
    await openSources();
    expect(screen.getByTestId("source-status-solarwinds").textContent).toBe("Last run failed");
    expect(screen.getByText("boom")).toBeTruthy();
  });

  it("Device reads: refresh all lists the devices that could not be read, manage only", async () => {
    refreshFacts.mockResolvedValue({
      ok: true,
      results: [
        { device: "a", status: "ok", platform: "ios", fields_seen: [], proposals: 0, conflicts: 0, unchanged: 0, pinned: 0, warnings: [] },
        { device: "br-jkt-rtr-01", status: "error", error: "timeout", platform: "ios", fields_seen: [], proposals: 0, conflicts: 0, unchanged: 0, pinned: 0, warnings: [] },
      ],
      batch_id: null,
      counts: {},
    });
    await openSources();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Refresh all active devices" }));
    });
    expect(refreshFacts).toHaveBeenCalledWith({});
    await waitFor(() => expect(screen.getByText("1 of 2 reachable")).toBeTruthy());
    expect(screen.getByText("br-jkt-rtr-01")).toBeTruthy();

    cleanup();
    session = { ...session, permissions: ["inventory.view"] };
    await openSources();
    expect(screen.queryByRole("button", { name: "Refresh all active devices" })).toBeNull();
  });
});
