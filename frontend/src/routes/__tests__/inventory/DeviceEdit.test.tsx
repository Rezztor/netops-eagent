// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as inventoryApi from "@/api/inventory";
import type { Device } from "@/api/inventory";
import * as placesApi from "@/api/inventoryPlaces";
import type { Place } from "@/api/inventoryPlaces";
import { ToastProvider } from "@/components";
import { DeviceEdit } from "../../inventory/DeviceEdit";

vi.mock("@/api/inventory", async (importOriginal) => ({
  ...(await importOriginal<typeof inventoryApi>()),
  listDevices: vi.fn(),
  getDevice: vi.fn(),
  proposeChange: vi.fn(),
  decideProposal: vi.fn(),
}));

vi.mock("@/api/inventoryPlaces", async (importOriginal) => ({
  ...(await importOriginal<typeof placesApi>()),
  listPlaces: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ permissions: ["inventory.view", "inventory.manage"] }) }));

const listPlaces = vi.mocked(placesApi.listPlaces);
const listDevices = vi.mocked(inventoryApi.listDevices);
const getDevice = vi.mocked(inventoryApi.getDevice);
const proposeChange = vi.mocked(inventoryApi.proposeChange);

function place(id: string, name: string): Place {
  const parts = id.split("-");
  return {
    id,
    parent_id: parts.length > 1 ? parts.slice(0, -1).join("-") : null,
    kind: placesApi.kindOf(id),
    code: parts[parts.length - 1]!,
    name,
    active: true,
    room_type: "",
    address: "",
    contact: "",
    notes: "",
    device_count: 0,
    device_total: 0,
  };
}

const ROOM = "LAB-M-M-L1-R01";
const PLACES = [
  place("LAB", "Lab"),
  place("LAB-M", "Main"),
  place("LAB-M-M", "Main"),
  place("LAB-M-M-L1", "Level 1"),
  place(ROOM, "Comms"),
  place(`${ROOM}-RK01`, "Rack 1"),
  place(`${ROOM}-RK02`, "Rack 2"),
];

const device = {
  id: "dev-1",
  name: "lab-ios",
  role: "access",
  vendor: "Cisco",
  platform: "ios",
  model: "IOSv",
  serial: "",
  asset_tag: "",
  status: "active",
  place_id: `${ROOM}-RK01`,
  site: { id: "LAB", name: "Lab" },
  mgmt_ip: "10.0.0.1",
  software_version: "",
  ha_group: "",
  tags: [],
  device_type: "switch",
  rack_unit: 18,
  custom: {},
  field_sources: {},
  version: 1,
} as unknown as Device;

const onDone = vi.fn();
const onCancel = vi.fn();

function renderEdit(deviceId: string | null, canApprove = false) {
  return render(
    <ToastProvider>
      <DeviceEdit deviceId={deviceId} canApprove={canApprove} onDone={onDone} onCancel={onCancel} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  listDevices.mockResolvedValue({ ok: true, devices: [], facets: { role: [] } } as never);
  listPlaces.mockResolvedValue({ ok: true, places: PLACES, room_types: [] });
  getDevice.mockResolvedValue({ ok: true, device });
  proposeChange.mockResolvedValue({ ok: true, proposal: { id: "p1" } } as never);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DeviceEdit", () => {
  it("add: sends a create proposal with JSON types and goes to Review", async () => {
    renderEdit(null);
    await waitFor(() => expect(screen.getByLabelText("Device name")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Device name"), { target: { value: "new-sw" } });
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "firewall" } });
    // rack unit and height belong to a rack: hidden until the place is one
    expect(screen.queryByLabelText("Rack unit")).toBeNull();
    fireEvent.change(screen.getByLabelText("Find a place"), { target: { value: "rack 2" } });
    fireEvent.click(screen.getByRole("option", { name: /Rack 2/ }));
    fireEvent.change(screen.getByLabelText("Rack unit"), { target: { value: "22" } });
    fireEvent.change(screen.getByLabelText("Warranty expires"), { target: { value: "2027-01-31" } });
    expect(screen.getByText(/Saving sends a request to add this device to Review/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send to Review" }));
    });
    await waitFor(() =>
      expect(proposeChange).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "create",
          fields: expect.objectContaining({
            name: "new-sw",
            device_type: "firewall",
            place_id: `${ROOM}-RK02`,
            rack_unit: 22,
            u_height: null,
            warranty_expiry: "2027-01-31",
            purchase_date: null,
          }),
        }),
      ),
    );
    expect(onDone).toHaveBeenCalled();
  });

  it("add: Create and approve proposes then approves, and only an approver sees it", async () => {
    vi.mocked(inventoryApi.decideProposal).mockResolvedValue({ ok: true } as never);
    const { unmount } = renderEdit(null);
    await waitFor(() => expect(screen.getByLabelText("Device name")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Create and approve" })).toBeNull();
    unmount();

    renderEdit(null, true);
    await waitFor(() => expect(screen.getByLabelText("Device name")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Device name"), { target: { value: "new-sw" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create and approve" }));
    });
    await waitFor(() => expect(inventoryApi.decideProposal).toHaveBeenCalledWith({ id: "p1", decision: "approve" }));
    expect(onDone).toHaveBeenCalled();
  });

  it("add: a name is required", async () => {
    renderEdit(null);
    await waitFor(() => expect(screen.getByLabelText("Device name")).toBeTruthy());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send to Review" }));
    });
    expect(proposeChange).not.toHaveBeenCalled();
  });

  it("edit: lists each change old to new, with the places as paths, and sends only the changed fields", async () => {
    renderEdit("dev-1");
    await waitFor(() => expect(screen.getByDisplayValue("lab-ios")).toBeTruthy());
    const send = screen.getByRole("button", { name: "Send to Review" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Rack unit"), { target: { value: "22" } });
    fireEvent.change(screen.getByLabelText("Find a place"), { target: { value: "rack 2" } });
    fireEvent.click(screen.getByRole("option", { name: /Rack 2/ }));
    const panel = screen.getByLabelText("Summary of your change");
    expect(panel.textContent).toMatch(/Rack unit18 to 22/);
    expect(panel.textContent).toContain(`${ROOM}-RK01`);
    expect(panel.textContent).toContain(`${ROOM}-RK02`);
    expect(panel.textContent).toMatch(/Saving sends 2 field changes to Review/);

    await act(async () => {
      fireEvent.click(send);
    });
    await waitFor(() =>
      expect(proposeChange).toHaveBeenCalledWith({
        kind: "update",
        device_id: "dev-1",
        fields: { place_id: `${ROOM}-RK02`, rack_unit: 22 },
      }),
    );
    expect(onDone).toHaveBeenCalled();
  });

  it("edit: moving out of a rack hides and clears the rack unit in the same change", async () => {
    renderEdit("dev-1");
    await waitFor(() => expect(screen.getByDisplayValue("lab-ios")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Find a place"), { target: { value: "comms" } });
    fireEvent.click(screen.getByRole("option", { name: new RegExp(`^${ROOM} Comms`) }));
    expect(screen.queryByLabelText("Rack unit")).toBeNull();
    expect(screen.getByLabelText("Summary of your change").textContent).toMatch(/Rack unit18 to not set/);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send to Review" }));
    });
    await waitFor(() =>
      expect(proposeChange).toHaveBeenCalledWith({
        kind: "update",
        device_id: "dev-1",
        fields: { place_id: ROOM, rack_unit: null },
      }),
    );
  });

  it("shows the record meter and what is missing", async () => {
    renderEdit("dev-1");
    await waitFor(() => expect(screen.getByDisplayValue("lab-ios")).toBeTruthy());
    expect(screen.getByText("Missing: serial")).toBeTruthy();
  });
});
