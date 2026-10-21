// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as inventoryApi from "@/api/inventory";
import * as placesApi from "@/api/inventoryPlaces";
import type { Place } from "@/api/inventoryPlaces";
import * as reportsApi from "@/api/inventoryReports";
import { ConfirmProvider, ToastProvider } from "@/components";
import { PlacesPanel } from "../../inventory/PlacesPanel";

vi.mock("@/api/inventoryReports", () => ({ getLifecycle: vi.fn(), getQuality: vi.fn(), getRacks: vi.fn() }));
vi.mock("@/api/inventory", async (importOriginal) => ({ ...(await importOriginal<typeof inventoryApi>()), listDevices: vi.fn() }));
vi.mock("@/api/inventoryPlaces", async (importOriginal) => ({
  ...(await importOriginal<typeof placesApi>()),
  listPlaces: vi.fn(),
  createPlace: vi.fn(),
  updatePlace: vi.fn(),
  deletePlace: vi.fn(),
  suggestCode: vi.fn(),
  mergePlaces: vi.fn(),
}));
let canPlaces = true;
vi.mock("@/lib/session", () => ({ useCan: () => canPlaces }));

const m = {
  getRacks: vi.mocked(reportsApi.getRacks),
  listDevices: vi.mocked(inventoryApi.listDevices),
  listPlaces: vi.mocked(placesApi.listPlaces),
  createPlace: vi.mocked(placesApi.createPlace),
  updatePlace: vi.mocked(placesApi.updatePlace),
  suggestCode: vi.mocked(placesApi.suggestCode),
  mergePlaces: vi.mocked(placesApi.mergePlaces),
};

const ROOM = "LAB-M-B-L1-R101";
const RACK = `${ROOM}-RK01`;
const KINDS = ["site", "location", "building", "floor", "room", "rack"] as const;
const place = (id: string, name: string, over: Partial<Place> = {}): Place => {
  const parts = id.split("-");
  return {
    id, parent_id: parts.length > 1 ? parts.slice(0, -1).join("-") : null, kind: KINDS[parts.length - 1]!, code: parts.at(-1)!, name,
    active: true, room_type: "", address: "", contact: "", notes: "", device_count: 0, device_total: 3, ...over,
  };
};
const tree = [
  place("LAB", "Lab"), place("LAB-M", "Main"), place("LAB-M-B", "Building"), place("LAB-M-B-L1", "Level 1"),
  place(ROOM, "Room 1"), place(RACK, "Rack 1"), place("LAB-M-B-L1-R102", "Room 2", { active: false }),
];

const device = (id: string, name: string, over: Record<string, unknown> = {}) =>
  ({ id, name, status: "active", site: null, tags: [], custom: {}, field_sources: {}, place_id: null, ...over }) as unknown as inventoryApi.Device;

const onOpenDevice = vi.fn();
function renderPlaces(url = "/inventory?tab=places", canManage = true) {
  render(
    <MemoryRouter initialEntries={[url]}>
      <ToastProvider>
        <ConfirmProvider>
          <PlacesPanel canManage={canManage} onOpenDevice={onOpenDevice} />
        </ConfirmProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  canPlaces = true;
  m.listPlaces.mockResolvedValue({ ok: true, places: tree, room_types: ["server_room", "office"] });
  m.getRacks.mockResolvedValue({
    ok: true,
    racks: [
      {
        place_id: RACK, site: "Lab", location: "Main", building: "Building", floor: "Level 1", room: "Room 1", rack: "Rack 1", used_u: 1,
        devices: [
          { device_id: "d3", name: "sw-a", device_type: "switch", model: "X", status: "active", rack_unit: 22, u_height: 1 },
          { device_id: "d4", name: "listed-only", device_type: "switch", model: "", status: "active", rack_unit: null, u_height: 1 },
        ],
      },
    ],
  });
  m.listDevices.mockImplementation(async (params) =>
    params?.place === "none"
      ? { ok: true, devices: [device("d6", "lab-sonic")], total: 1 }
      : { ok: true, devices: [device("d5", "spare", { place_id: ROOM }), device("d3", "sw-a", { place_id: RACK })], total: 2 },
  );
  m.createPlace.mockResolvedValue({ ok: true, place: place(`${ROOM}-RK02`, "Rack 2") });
  m.updatePlace.mockResolvedValue({ ok: true, place: tree[4]! });
  m.suggestCode.mockResolvedValue({ ok: true, code: "RK02" });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PlacesPanel", () => {
  it("renders six levels, selects from the URL, and draws the racks under the room", async () => {
    renderPlaces(`/inventory?tab=places&place=${ROOM}`);
    await waitFor(() => expect(screen.getByTestId("places-panel")).toBeTruthy());
    for (const p of tree.filter((x) => x.active)) expect(screen.getByTestId(`place-${p.id}`)).toBeTruthy();
    expect(screen.getByTestId(`place-${ROOM}`).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("place-LAB").getAttribute("aria-selected")).toBe("false");
    expect(screen.queryByTestId("place-LAB-M-B-L1-R102")).toBeNull(); // inactive stays hidden until asked for
    fireEvent.click(screen.getByLabelText("Show inactive"));
    expect(within(screen.getByTestId("place-LAB-M-B-L1-R102")).getByText("inactive")).toBeTruthy();
    await waitFor(() => expect(m.getRacks).toHaveBeenCalledWith({ place: ROOM }));
    fireEvent.click(await screen.findByTestId("rack-open-d3"));
    expect(onOpenDevice).toHaveBeenCalledWith("d3");
    expect(screen.getByText("listed-only")).toBeTruthy();
    expect(screen.getByTestId("racks-unplaced").textContent).toContain("spare");
  });

  it("lists devices with no place and opens one", async () => {
    renderPlaces();
    await waitFor(() => expect(screen.getByTestId("place-none")).toBeTruthy());
    fireEvent.click(screen.getByTestId("place-none"));
    fireEvent.click(await screen.findByTestId("place-nosite-d6"));
    expect(onOpenDevice).toHaveBeenCalledWith("d6");
  });

  it("adds a rack under the room through the API", async () => {
    renderPlaces(`/inventory?tab=places&place=${ROOM}`);
    fireEvent.click(await screen.findByTestId("place-add-child"));
    fireEvent.change(screen.getByLabelText("Place name"), { target: { value: "Rack 2" } });
    await waitFor(() => expect((screen.getByLabelText("Place code") as HTMLInputElement).value).toBe("RK02")); // as you type, no blur
    expect(screen.getByText(`${ROOM}-RK02`)).toBeTruthy(); // the ID the code makes
    fireEvent.click(screen.getByRole("button", { name: "Create rack" }));
    await waitFor(() => expect(m.createPlace).toHaveBeenCalledWith(expect.objectContaining({ parent_id: ROOM, name: "Rack 2", code: "RK02" })));
  });

  it("renames and marks inactive through the API", async () => {
    renderPlaces(`/inventory?tab=places&place=${ROOM}`);
    fireEvent.click(await screen.findByTestId("place-rename"));
    fireEvent.change(screen.getByLabelText("Place name"), { target: { value: "Server room" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(m.updatePlace).toHaveBeenCalledWith(ROOM, expect.objectContaining({ name: "Server room" })));

    fireEvent.click(screen.getByTestId("place-active"));
    await waitFor(() => expect(m.updatePlace).toHaveBeenCalledWith(ROOM, { active: false }));
  });

  it("sends a chain for a small site", async () => {
    renderPlaces();
    fireEvent.click(await screen.findByTestId("place-add-site"));
    fireEvent.change(screen.getByLabelText("Place name"), { target: { value: "Jebel" } });
    fireEvent.change(screen.getByLabelText("Place code"), { target: { value: "JBL" } });
    fireEvent.click(screen.getByLabelText("Small site"));
    fireEvent.click(screen.getByRole("button", { name: "Create site" }));
    await waitFor(() =>
      expect(m.createPlace).toHaveBeenCalledWith(
        expect.objectContaining({ parent_id: null, code: "JBL", chain: [{ name: "Main", code: "M" }, { name: "Main", code: "M" }] }),
      ),
    );
  });

  it("offers the upper three levels only with inventory.places", async () => {
    canPlaces = false;
    renderPlaces("/inventory?tab=places&place=LAB");
    await screen.findByTestId("place-LAB");
    expect(screen.queryByTestId("place-add-site")).toBeNull();
    expect(screen.queryByTestId("place-add-child")).toBeNull(); // a location under a site
    expect(screen.queryByTestId("place-rename")).toBeNull();
    expect(screen.queryByTestId("place-delete")).toBeNull();
    fireEvent.click(screen.getByTestId("place-LAB-M-B-L1")); // a floor takes a room: inventory.manage is enough
    expect(screen.getByTestId("place-add-child").textContent).toBe("Add room");
    expect(screen.getByTestId("place-rename")).toBeTruthy();
    expect(screen.queryByTestId("place-delete")).toBeNull();
  });

  it("stops suggesting once the code is typed, and refuses a code or name already in use", async () => {
    renderPlaces(`/inventory?tab=places&place=${ROOM}`);
    fireEvent.click(await screen.findByTestId("place-add-child"));
    fireEvent.change(screen.getByLabelText("Place name"), { target: { value: "Rack 2" } });
    await waitFor(() => expect((screen.getByLabelText("Place code") as HTMLInputElement).value).toBe("RK02"));
    fireEvent.change(screen.getByLabelText("Place code"), { target: { value: "rk 01" } });
    expect((screen.getByLabelText("Place code") as HTMLInputElement).value).toBe("RK01");
    expect((await screen.findByRole("alert")).textContent).toContain(`Already used by Rack 1 (${RACK})`);
    expect((screen.getByRole("button", { name: "Create rack" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Place name"), { target: { value: "Rack 3" } });
    await new Promise((r) => setTimeout(r, 400));
    expect((screen.getByLabelText("Place code") as HTMLInputElement).value).toBe("RK01"); // typed by hand: kept
  });

  it("merges a place into another of the same kind: preview first, then confirm", async () => {
    const two = [...tree, place("LAB-M-B-L1-R103", "Room 3")];
    m.listPlaces.mockResolvedValue({ ok: true, places: two, room_types: [] });
    const plan = {
      ok: true, from_id: ROOM, into_id: "LAB-M-B-L1-R103", dry_run: true, kind: "room",
      create: [{ id: "LAB-M-B-L1-R103-RK01", name: "Rack 1", kind: "rack" }], clashes: [], devices: 2, redirected: 1,
      sample: [{ device_id: "d3", name: "sw-a", from: RACK, to: "LAB-M-B-L1-R103-RK01" }],
    };
    m.mergePlaces.mockResolvedValue(plan as placesApi.MergeResult);
    renderPlaces(`/inventory?tab=places&place=${ROOM}`);
    fireEvent.click(await screen.findByTestId("place-merge"));
    const choose = screen.getByLabelText("Merge into") as HTMLSelectElement;
    expect([...choose.options].map((o) => o.value)).toEqual(["", "LAB-M-B-L1-R103"]); // same kind, active, not itself
    fireEvent.change(choose, { target: { value: "LAB-M-B-L1-R103" } });
    expect((await screen.findByTestId("merge-plan")).textContent).toContain("2 devices would move");
    expect(m.mergePlaces).toHaveBeenLastCalledWith(ROOM, "LAB-M-B-L1-R103", true);
    m.mergePlaces.mockResolvedValue({ ...plan, dry_run: false, batch_id: "b1" } as placesApi.MergeResult);
    fireEvent.click(screen.getByTestId("merge-apply"));
    await waitFor(() => expect(m.mergePlaces).toHaveBeenLastCalledWith(ROOM, "LAB-M-B-L1-R103", false));
  });

  it("offers no merge without inventory.places", async () => {
    canPlaces = false;
    renderPlaces(`/inventory?tab=places&place=${ROOM}`);
    await screen.findByTestId("place-rename");
    expect(screen.queryByTestId("place-merge")).toBeNull();
  });
});
