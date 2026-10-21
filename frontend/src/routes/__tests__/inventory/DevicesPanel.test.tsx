// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as inventoryApi from "@/api/inventory";
import type { Device } from "@/api/inventory";
import * as placesApi from "@/api/inventoryPlaces";
import type { Place } from "@/api/inventoryPlaces";
import { ToastProvider } from "@/components";
import { DevicesPanel } from "../../inventory/DevicesPanel";

vi.mock("@/api/inventory", async (importOriginal) => ({
  ...(await importOriginal<typeof inventoryApi>()),
  listDevices: vi.fn(),
  syncInventory: vi.fn(),
}));
vi.mock("@/api/inventoryPlaces", async (importOriginal) => ({
  ...(await importOriginal<typeof placesApi>()),
  listPlaces: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useCan: () => true }));
const place = (id: string, name: string): Place => ({
  id, parent_id: id.includes("-") ? id.slice(0, id.lastIndexOf("-")) : null, kind: "site", code: id.split("-").pop()!, name,
  active: true, room_type: "", address: "", contact: "", notes: "", device_count: 0, device_total: 0,
});
const listDevices = vi.mocked(inventoryApi.listDevices);
const syncInventory = vi.mocked(inventoryApi.syncInventory);

const life = { date: null, days: null, state: "none" as const };
const device: Device = {
  id: "d1", name: "lab-ios", role: "core", vendor: "Cisco", platform: "ios", model: "IOSv", serial: "FCW1",
  asset_tag: "", status: "active", site: { id: "s1", name: "HQ" }, mgmt_ip: "10.0.0.1",
  software_version: "15.9", ha_group: "", tags: ["lab"], device_type: "switch", room: "A-1", rack: "R01",
  rack_unit: 40, custodian: "Network team", warranty_expiry: "2026-11-09", custom: {},
  field_sources: { serial: { source: "facts", approved_by: "a", approved_at: "", observed_at: null } },
  last_seen_at: null, version: 1, created_at: "", updated_at: "2026-10-01T00:00:00Z",
  computed: {
    access_method: "ssh", monitored: true, completeness: { percent: 89, missing: [] },
    lifecycle: { warranty: { date: "2026-11-09", days: 38, state: "soon" }, support: life, eol: life },
    lifecycle_state: "soon",
  },
};
const retired: Device = { ...device, id: "d2", name: "old-sw", status: "decommissioned" };
const facets = { vendor: ["Cisco"], platform: ["ios"], status: ["active"], site: [{ id: "s1", name: "HQ" }], tag: ["lab"], role: ["core"] };

const store = new Map<string, string>();
Object.defineProperty(globalThis, "localStorage", {
  value: {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  },
  configurable: true,
});

const onOpenRecord = vi.fn();
const onAdd = vi.fn();
function renderPanel(url = "/inventory", props: { canManage?: boolean; canApprove?: boolean } = {}) {
  render(
    <MemoryRouter initialEntries={[url]}>
      <ToastProvider>
        <DevicesPanel canManage={props.canManage ?? true} canApprove={props.canApprove ?? true} onOpenRecord={onOpenRecord} onAdd={onAdd} />
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(placesApi.listPlaces).mockResolvedValue({
    ok: true, room_types: [],
    places: [place("HQ", "Headquarters"), place("HQ-GA", "Gate Avenue")],
  });
  listDevices.mockResolvedValue({ ok: true, devices: [device, retired], total: 2, facets });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DevicesPanel", () => {
  it("hides decommissioned devices unless the status filter asks, and shows place and lifecycle in words", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText("lab-ios")).toBeTruthy());
    expect(screen.queryByText("old-sw")).toBeNull();
    expect(screen.getByText("HQ · A-1 · R01 U40")).toBeTruthy();
    expect(screen.getByText("Warranty ends in 38 days")).toBeTruthy();
    expect(screen.getByText(/decommissioned devices are hidden/)).toBeTruthy();
    cleanup();
    renderPanel("/inventory?status=decommissioned");
    await waitFor(() => expect(screen.getByText("old-sw")).toBeTruthy());
  });

  it("sends the toolbar and menu filters in the list request", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText("lab-ios")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Search devices"), { target: { value: "lab" } });
    fireEvent.click(screen.getByRole("button", { name: "Place: All" }));
    fireEvent.change(await screen.findByLabelText("Find a place"), { target: { value: "gate" } });
    fireEvent.click(await screen.findByRole("option", { name: /HQ-GA/ }));
    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "active" } });
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    fireEvent.change(screen.getByLabelText("Filter by vendor"), { target: { value: "Cisco" } });
    fireEvent.change(screen.getByLabelText("Filter by role"), { target: { value: "core" } });
    fireEvent.click(screen.getByLabelText("Stale only"));
    await waitFor(() =>
      expect(listDevices).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "lab", place: "HQ-GA", status: "active", vendor: "Cisco", role: "core", stale: true, limit: 50, offset: 0 }),
      ),
    );
  });

  it("opens the summary with the seven facts, then the record or the edit form", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText("lab-ios")).toBeTruthy());
    fireEvent.click(screen.getByText("lab-ios"));
    const summary = screen.getByRole("complementary", { name: "Device summary" });
    for (const label of ["Type", "Model", "Serial", "Management IP", "Software", "Place", "Custodian"]) {
      expect(within(summary).getByText(label)).toBeTruthy();
    }
    expect(within(summary).getByText("Cisco IOSv")).toBeTruthy();
    expect(within(summary).getByText("old place text")).toBeTruthy(); // no place picked yet: the typed text, labelled
    expect(within(summary).getByText("device")).toBeTruthy(); // the serial came from the device
    expect(within(summary).getByText("Warranty ends in 38 days")).toBeTruthy();
    fireEvent.click(within(summary).getByRole("button", { name: "Open full record" }));
    expect(onOpenRecord).toHaveBeenCalledWith("d1");
    fireEvent.click(within(summary).getByRole("button", { name: "Close summary" }));
    expect(screen.queryByRole("complementary", { name: "Device summary" })).toBeNull();
  });

  it("switches column sets and keeps a column choice in localStorage", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText("lab-ios")).toBeTruthy());
    expect(screen.queryByRole("columnheader", { name: "Serial" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Hardware" }));
    expect(screen.getByRole("columnheader", { name: "Serial" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Choose columns" }));
    fireEvent.click(screen.getByLabelText("Column Rack"));
    expect(screen.getByRole("columnheader", { name: "Rack" })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("eagent.inventory.columns") ?? "[]")).toContain("rack");
  });

  it("swaps the toolbar for the bulk bar when rows are selected", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText("lab-ios")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("Select lab-ios"));
    const bar = screen.getByRole("toolbar", { name: "Actions for selected devices" });
    expect(within(bar).getByText("1 device selected")).toBeTruthy();
    expect(screen.queryByLabelText("Search devices")).toBeNull();
    expect(screen.queryByRole("complementary", { name: "Device summary" })).toBeNull(); // the checkbox is not a row click
    fireEvent.click(within(bar).getByRole("button", { name: "Edit fields" }));
    expect(screen.getByRole("dialog", { name: "Edit 1 device" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(bar).getByRole("button", { name: "Clear" }));
    expect(screen.getByLabelText("Search devices")).toBeTruthy();
  });

  it("keeps the executor sync in the overflow menu, only with approve", async () => {
    syncInventory.mockResolvedValue({ ok: true, diff: { added: ["lab-ios"], removed: [], changed: [], warnings: [] } });
    renderPanel();
    await waitFor(() => expect(screen.getByText("lab-ios")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sync to executor" }));
    await waitFor(() => expect(syncInventory).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.getByRole("dialog", { name: "Sync to executor" })).toBeTruthy());
    cleanup();
    renderPanel("/inventory", { canApprove: false });
    await waitFor(() => expect(screen.getByText("lab-ios")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.queryByRole("menuitem", { name: "Sync to executor" })).toBeNull();
  });

  it("hides selection without inventory.manage and offers Add device on an empty list", async () => {
    listDevices.mockResolvedValue({ ok: true, devices: [], total: 0, facets });
    renderPanel("/inventory", { canManage: false });
    await waitFor(() => expect(screen.getByText(/No devices yet/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Add device" })).toBeNull();
    cleanup();
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Add device" }));
    expect(onAdd).toHaveBeenCalled();
  });

  it("shows an amber stale badge next to the status", async () => {
    listDevices.mockResolvedValue({
      ok: true,
      devices: [{ ...device, sources: [{ source: "solarwinds", source_id: "42", stale: true, last_seen_at: new Date(Date.now() - 9 * 86_400_000).toISOString() }] }],
      total: 1,
      facets,
    });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("stale-d1").textContent).toBe("stale 9d"));
  });
});
