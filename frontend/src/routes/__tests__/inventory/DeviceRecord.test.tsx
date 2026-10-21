// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as inventoryApi from "@/api/inventory";
import * as backupsApi from "@/api/inventoryBackups";
import * as portsApi from "@/api/inventoryPorts";
import * as reportsApi from "@/api/inventoryReports";
import * as networkApi from "@/api/networkConsole";
import type { Device, Proposal } from "@/api/inventory";
import { ConfirmProvider, ToastProvider } from "@/components";
import { DeviceRecord } from "../../inventory/DeviceRecord";

vi.mock("@/api/inventory", async (importOriginal) => ({
  ...(await importOriginal<typeof inventoryApi>()),
  getDevice: vi.fn(),
  proposeChange: vi.fn(),
  refreshFacts: vi.fn(),
}));
vi.mock("@/api/inventoryReports", async (importOriginal) => ({
  ...(await importOriginal<typeof reportsApi>()),
  getRacks: vi.fn(),
}));
vi.mock("@/api/inventoryPlaces", () => ({
  listPlaces: vi.fn().mockResolvedValue({ ok: true, places: [], room_types: [] }),
  ancestorsOf: (id: string) => id.split("-").map((_, i, parts) => parts.slice(0, i + 1).join("-")),
  searchPlaces: () => [],
}));
vi.mock("@/api/inventoryPorts", () => ({ getPorts: vi.fn(), refreshPorts: vi.fn(), markUplink: vi.fn() }));
vi.mock("@/api/inventoryBackups", () => ({ listBackups: vi.fn(), runBackup: vi.fn(), viewBackup: vi.fn(), diffBackup: vi.fn() }));
vi.mock("@/api/networkConsole", () => ({ getNetworks: vi.fn() }));
vi.mock("@/lib/session", () => ({ useSession: () => session }));

const session = {
  preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} },
};

const m = {
  getDevice: vi.mocked(inventoryApi.getDevice),
  proposeChange: vi.mocked(inventoryApi.proposeChange),
  refreshFacts: vi.mocked(inventoryApi.refreshFacts),
  getRacks: vi.mocked(reportsApi.getRacks),
  getNetworks: vi.mocked(networkApi.getNetworks),
  getPorts: vi.mocked(portsApi.getPorts),
  refreshPorts: vi.mocked(portsApi.refreshPorts),
  markUplink: vi.mocked(portsApi.markUplink),
  listBackups: vi.mocked(backupsApi.listBackups),
  runBackup: vi.mocked(backupsApi.runBackup),
  viewBackup: vi.mocked(backupsApi.viewBackup),
  diffBackup: vi.mocked(backupsApi.diffBackup),
};

const port = (name: string, extra: Partial<portsApi.PortRow> = {}): portsApi.PortRow => ({
  name, alias: "", description: "", admin_up: true, oper_up: true, speed: "1G", media: "", present: true, poe_watts: null,
  mode: "access", vlans: [10], ip_prefixes: [], mac: "", neighbor: null, is_uplink: false, uplink_source: "", ...extra,
});
const portsReply: portsApi.PortsResponse = {
  ok: true,
  observed_at: "2026-10-02T08:00:00Z",
  interfaces: [
    port("Ethernet0", { description: "to core", neighbor: { name: "core-sw", port: "Eth1/1", device_id: "core" }, is_uplink: true, uplink_source: "neighbour" }),
    port("Ethernet1", { oper_up: false }),
  ],
  components: [{ kind: "psu", name: "PSU 1", part_number: "PWR-1", serial: "S1", description: "", expires_at: null }],
  summary: {
    total: 2, up: 1, down: 1, unknown: 0, poe: null, optics: [{ media: "SFP+", count: 1 }], vlans: [10, 11, 12],
    mgmt_subnet: "10.0.0.0/24", uplinks: [{ port: "Ethernet0", neighbor: "core-sw", neighbor_port: "Eth1/1", device_id: "core" }],
  },
};

const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const lifeDate = (days: number, state: "ok" | "soon" | "expired") => ({ date: day(days), days, state });

const device: Device = {
  id: "dev-1",
  name: "fw-a",
  role: "edge",
  vendor: "Fortinet",
  platform: "fortios",
  model: "FG-100F",
  serial: "",
  asset_tag: "",
  status: "active",
  site: { id: "site-1", name: "HQ" },
  mgmt_ip: "10.0.0.1",
  software_version: "7.2",
  ha_group: "cluster-1",
  tags: ["hq"],
  device_type: "firewall",
  place_id: "HQ-M-B1-L1-R01-RK01",
  place: {
    id: "HQ-M-B1-L1-R01-RK01",
    kind: "rack",
    active: true,
    path: [
      { id: "HQ", kind: "site", code: "HQ", name: "HQ" },
      { id: "HQ-M-B1-L1-R01-RK01", kind: "rack", code: "RK01", name: "Rack 1" },
    ],
  },
  rack: "Rack 1",
  rack_unit: 36,
  u_height: 1,
  warranty_expiry: day(38),
  support_expiry: day(200),
  eol_date: day(900),
  purchase_date: day(-1000),
  custom: {},
  field_sources: {
    software_version: { source: "facts", approved_by: "asha", approved_at: "2026-01-01T00:00:00Z", observed_at: null },
    model: { source: "manual", approved_by: "asha", approved_at: "2026-01-01T00:00:00Z", observed_at: null },
  },
  sources: [{ source: "solarwinds", source_id: "42", last_seen_at: "2026-01-02T00:00:00Z", stale: false }],
  last_seen_at: "2026-01-02T00:00:00Z",
  facts_at: null,
  ha_members: [{ id: "dev-2", name: "fw-b", status: "active" }],
  computed: {
    access_method: "ssh",
    monitored: true,
    completeness: { percent: 78, missing: ["serial"] },
    lifecycle: { warranty: lifeDate(38, "soon"), support: lifeDate(200, "ok"), eol: lifeDate(900, "ok") },
    lifecycle_state: "soon",
  },
  version: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const proposal = (id: string, status: Proposal["status"]): Proposal => ({
  id,
  kind: "update",
  device_id: "dev-1",
  device_name: "fw-a",
  base_version: 1,
  changes: { support_contract: { old: "", new: "SC-1" } },
  source: "manual",
  evidence: null,
  status,
  requested_by: "asha",
  requested_at: "2026-01-03T00:00:00Z",
  decided_by: null,
  decided_at: null,
  decision_note: null,
});

const props = {
  deviceId: "dev-1",
  canManage: true,
  canApprove: true,
  onBack: vi.fn(),
  onEdit: vi.fn(),
  onOpenDevice: vi.fn(),
};

async function renderRecord(overrides: Partial<typeof props> = {}) {
  render(
    <MemoryRouter>
      <ToastProvider>
        <ConfirmProvider>
          <DeviceRecord {...props} {...overrides} />
        </ConfirmProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
  await screen.findByTestId("device-record");
}

const noNetworks = { ok: true, known: false, generated_at: null, networks: [], hint: "", labels: {} };

beforeEach(() => {
  m.getNetworks.mockResolvedValue(noNetworks);
  m.getDevice.mockResolvedValue({ ok: true, device, observations: [], proposals: [proposal("p1", "pending")] });
  m.getRacks.mockResolvedValue({
    ok: true,
    unplaced: 0,
    racks: [
      {
        place_id: "HQ-M-B1-L1-R01-RK01", site: "HQ", location: "", building: "", floor: "", room: "", rack: "Rack 1", used_u: 2,
        devices: [
          { device_id: "core", name: "core-sw", device_type: "switch", model: "", status: "active", rack_unit: 40, u_height: 1 },
          { device_id: "dev-1", name: "fw-a", device_type: "firewall", model: "", status: "active", rack_unit: 36, u_height: 1 },
        ],
      },
    ],
  });
  m.refreshFacts.mockResolvedValue({
    ok: true,
    results: [
      {
        device: "fw-a", status: "ok", platform: "fortios", fields_seen: ["model", "serial"],
        proposals: 1, conflicts: 1, unchanged: 0, pinned: 0, warnings: ["hostname fw-a2 differs"],
      },
    ],
    batch_id: "b-2",
    counts: {},
  });
  m.proposeChange.mockResolvedValue({ ok: true });
  m.getPorts.mockResolvedValue(portsReply);
  m.refreshPorts.mockResolvedValue({ ok: true, refreshed: 1, failed: [] });
  m.listBackups.mockResolvedValue({
    ok: true,
    backups: [
      { id: "b2", taken_at: "2026-10-02T06:00:00Z", checked_at: "2026-10-02T07:00:00Z", sha256: "b".repeat(64), size: 2048 },
      { id: "b1", taken_at: "2026-10-01T06:00:00Z", checked_at: null, sha256: "a".repeat(64), size: 2000 },
    ],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DeviceRecord", () => {
  it("shows the network beside the management address when the appliance knows it, and nothing when it does not", async () => {
    m.getNetworks.mockResolvedValue({ ...noNetworks, known: true, labels: { "10.0.0.1": "VLAN 200, routed" } });
    await renderRecord();
    await screen.findByText(/\(VLAN 200, routed\)/);
    expect(m.getNetworks).toHaveBeenCalledWith(["10.0.0.1"]);
    cleanup();
    m.getNetworks.mockRejectedValue(new Error("403"));
    await renderRecord();
    expect(screen.queryByText(/VLAN/)).toBeNull();
  });

  it("shows the header, tiles and cards, with a source only where a person did not set the value", async () => {
    await renderRecord();
    expect(screen.getByRole("heading", { name: "fw-a" })).toBeTruthy();
    expect(screen.getByText("Warranty ends in 38 days")).toBeTruthy();
    expect(screen.getByText("78% complete")).toBeTruthy();
    expect(screen.getByText("Missing serial")).toBeTruthy();
    expect(screen.getByText("Monitored")).toBeTruthy();
    expect(screen.getByText("U36, 1U high")).toBeTruthy();
    expect(screen.getByText("HQ-M-B1-L1-R01-RK01")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open in Places" }).getAttribute("href")).toContain("place=HQ-M-B1-L1-R01-RK01");
    expect(screen.getByText("computed")).toBeTruthy();
    // software came from the device, model from a person
    expect(screen.getAllByText("device", { selector: "span" }).length).toBeGreaterThan(0);
    expect(screen.queryByText("manual")).toBeNull();
    expect(screen.getByTestId("lifecycle-track")).toBeTruthy();
    expect((await screen.findByTestId("rack-strip")).textContent).toBe("U40 core-sw" + "U36 fw-a");
  });

  it("shows the old text kept in custom, labelled, while the device has no place", async () => {
    m.getDevice.mockResolvedValue({
      ok: true,
      device: { ...device, place_id: null, place: null, custom: { old_place_text: "Lab / 1 / 1.01 / R01 U22" } },
      observations: [],
      proposals: [],
    });
    await renderRecord();
    expect(screen.getByText("Old place text")).toBeTruthy();
    expect(screen.getByText("Lab / 1 / 1.01 / R01 U22")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Open in Places" })).toBeNull();
  });

  it("shows the computed subnet and uplink on the Network card, and the ports on the Ports tab", async () => {
    await renderRecord();
    expect(await screen.findByText("10.0.0.0/24")).toBeTruthy();
    expect(screen.getByText("Ethernet0 to core-sw Eth1/1")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /Ports/ }));
    const table = await screen.findByTestId("device-ports");
    expect(table.textContent).toContain("1 of 2");
    expect(table.textContent).toContain("10-12");
    expect(within(table).getByTestId("port-Ethernet1").textContent).toContain("down");
    fireEvent.click(within(table).getByRole("button", { name: "core-sw" }));
    expect(props.onOpenDevice).toHaveBeenCalledWith("core");
    // a neighbour-derived uplink has no toggle; a plain port does
    expect(within(table).queryByRole("button", { name: /Ethernet0 as uplink/ })).toBeNull();
    m.markUplink.mockResolvedValue({ ok: true });
    fireEvent.click(within(table).getByRole("button", { name: "Mark Ethernet1 as uplink" }));
    await waitFor(() => expect(m.markUplink).toHaveBeenCalledWith({ device_id: "dev-1", name: "Ethernet1", is_uplink: true }));
  });

  it("says so when no ports have been read, and offers no toggle without manage", async () => {
    m.getPorts.mockResolvedValue({ ok: true, observed_at: null, interfaces: [], components: [] });
    await renderRecord();
    fireEvent.click(screen.getByRole("tab", { name: /Ports/ }));
    expect((await screen.findByText(/have not been read/)).textContent).toContain("Read from device");
    cleanup();
    m.getPorts.mockResolvedValue(portsReply);
    await renderRecord({ canManage: false });
    fireEvent.click(screen.getByRole("tab", { name: /Ports/ }));
    await screen.findByTestId("device-ports");
    expect(screen.queryByRole("button", { name: /as uplink/ })).toBeNull();
  });

  it("backs up, lists, views the redacted text and compares the two picked backups oldest first", async () => {
    await renderRecord();
    fireEvent.click(screen.getByRole("tab", { name: "Backups" }));
    const panel = await screen.findByTestId("device-backups");
    expect(panel.textContent).toContain("2.0 KB");
    m.runBackup.mockResolvedValue({ ok: true, results: [{ device_id: "dev-1", name: "fw-a", ok: true, id: "b2", changed: false }] });
    fireEvent.click(within(panel).getByRole("button", { name: "Back up now" }));
    await waitFor(() => expect(m.runBackup).toHaveBeenCalledWith(["dev-1"]));
    expect(await screen.findByText("Unchanged since the last backup.")).toBeTruthy();
    m.viewBackup.mockResolvedValue({ ok: true, text: "hostname fw-a\nsecret ******" });
    fireEvent.click(within(panel).getByRole("button", { name: "View backup b2" }));
    expect((await screen.findByTestId("backup-text")).textContent).toContain("secret ******");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    m.diffBackup.mockResolvedValue({ ok: true, diff: "-a\n+b", changed_lines: 2 });
    fireEvent.click(within(panel).getByLabelText("Select backup b2"));
    fireEvent.click(within(panel).getByLabelText("Select backup b1"));
    fireEvent.click(within(panel).getByRole("button", { name: "Compare selected" }));
    await waitFor(() => expect(m.diffBackup).toHaveBeenCalledWith("dev-1", "b1", "b2"));
    expect((await screen.findByTestId("backup-text")).textContent).toBe("-a\n+b");
  });

  it("lists the device's sources and opens an HA member", async () => {
    await renderRecord();
    expect(screen.getByTestId("device-sources").textContent).toMatch(/solarwinds.*42/);
    const ha = screen.getByTestId("device-ha");
    expect(ha.textContent).toContain("cluster-1");
    fireEvent.click(within(ha).getByRole("button", { name: "fw-b" }));
    expect(props.onOpenDevice).toHaveBeenCalledWith("dev-2");
  });

  it("reads from the device and shows the per-device summary", async () => {
    await renderRecord();
    expect(screen.getByText(/never collected/)).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Read from device" }));
    });
    expect(m.refreshFacts).toHaveBeenCalledWith({ devices: ["fw-a"] });
    expect(m.refreshPorts).toHaveBeenCalledWith({ devices: ["fw-a"] });
    const result = await screen.findByTestId("facts-result");
    expect(result.textContent).toContain("1 proposals · 1 conflicts");
    expect(screen.getByTestId("facts-warnings").textContent).toContain("hostname fw-a2 differs");
  });

  it("shows a per-device error and a request-level refusal", async () => {
    m.refreshFacts.mockResolvedValueOnce({
      ok: true,
      results: [{ device: "fw-a", status: "error", error: "not in executor inventory", platform: "", fields_seen: [], proposals: 0, conflicts: 0, unchanged: 0, pinned: 0, warnings: [] }],
    });
    await renderRecord();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Read from device" }));
    });
    expect((await screen.findByTestId("facts-result")).textContent).toContain("not in executor inventory");
    expect(m.refreshPorts).not.toHaveBeenCalled();
    m.refreshFacts.mockResolvedValueOnce({ ok: false, error: "executor unreachable" });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Read from device" }));
    });
    expect((await screen.findByTestId("facts-error")).textContent).toBe("executor unreachable");
  });

  it("hides Read, Edit and Decommission without inventory.manage", async () => {
    await renderRecord({ canManage: false });
    for (const name of ["Read from device", "Edit", "More actions"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("asks about the device in a new chat, without inventory.manage too", async () => {
    await renderRecord({ canManage: false });
    const ask = screen.getByRole("link", { name: "Ask about this device" });
    expect(ask.getAttribute("href")).toBe(`/chat?device=${encodeURIComponent(device.id)}`);
  });

  it("goes to Edit and back", async () => {
    await renderRecord();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(props.onEdit).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Devices" }));
    expect(props.onBack).toHaveBeenCalled();
  });

  it("proposes a decommission after confirming", async () => {
    await renderRecord();
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Decommission" }));
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Propose decommission" }));
    });
    await waitFor(() =>
      expect(m.proposeChange).toHaveBeenCalledWith({
        kind: "decommission",
        device_id: "dev-1",
        fields: { status: "decommissioned" },
      }),
    );
  });

  it("lists the device's proposals on the Changes tab", async () => {
    await renderRecord();
    fireEvent.click(screen.getByRole("tab", { name: /Changes/ }));
    expect(screen.getByText("Support contract empty to SC-1")).toBeTruthy();
    expect(screen.getByText("pending")).toBeTruthy();
  });

  it("says so when the device cannot be loaded", async () => {
    m.getDevice.mockResolvedValue({ ok: false, error: "not found" });
    render(
      <MemoryRouter>
        <ToastProvider>
          <ConfirmProvider>
            <DeviceRecord {...props} />
          </ConfirmProvider>
        </ToastProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Could not load device")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back to devices" }));
    expect(props.onBack).toHaveBeenCalled();
  });
});
