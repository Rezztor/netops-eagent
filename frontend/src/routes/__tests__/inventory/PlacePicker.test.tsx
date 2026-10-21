// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as placesApi from "@/api/inventoryPlaces";
import type { Place } from "@/api/inventoryPlaces";
import { PlacePath, PlacePicker } from "../../inventory/PlacePicker";

vi.mock("@/api/inventoryPlaces", async (importOriginal) => ({
  ...(await importOriginal<typeof placesApi>()),
  createPlace: vi.fn(),
  suggestCode: vi.fn(),
}));
const createPlace = vi.mocked(placesApi.createPlace);
const suggestCode = vi.mocked(placesApi.suggestCode);

function place(id: string, name: string, active = true): Place {
  const parts = id.split("-");
  return {
    id,
    parent_id: parts.length > 1 ? parts.slice(0, -1).join("-") : null,
    kind: placesApi.kindOf(id),
    code: parts[parts.length - 1]!,
    name,
    active,
    room_type: "",
    address: "",
    contact: "",
    notes: "",
    device_count: 0,
    device_total: 0,
  };
}

const FLOOR = "DIFC-GA-GV4-L1";
const PLACES = [
  place("DIFC", "DIFC"),
  place("DIFC-GA", "Gate Avenue"),
  place("DIFC-GB", "Gate Boulevard"),
  place("DIFC-GA-GV4", "Gate Village Building 4"),
  place(FLOOR, "Level 1"),
  place(`${FLOOR}-R01`, "Room 1"),
  place(`${FLOOR}-R02`, "Room 2"),
  place(`${FLOOR}-R02-RK01`, "Rack 1"),
  place("JBR", "Jumeirah"),
  place("JBR-M", "Main"),
  place("JBR-M-M", "Main"),
  place("JBR-M-M-GF", "Ground floor"),
  place("JBR-M-M-GF-R01", "Comms"),
  place("JBR-M-M-GF-R01-RK01", "Rack 1"),
];
const ADMIN = { upper: true, lower: true };

const onChange = vi.fn();
const onCreated = vi.fn();

function Harness({ start = null, canCreate = ADMIN }: { start?: string | null; canCreate?: { upper: boolean; lower: boolean } }) {
  const [value, setValue] = useState<string | null>(start);
  return (
    <PlacePicker
      value={value}
      onChange={(v) => {
        onChange(v);
        setValue(v);
      }}
      places={PLACES}
      canCreate={canCreate}
      onCreated={onCreated}
    />
  );
}

const box = (name: string) => screen.getByRole("combobox", { name });
const open = (name: string, text?: string) => {
  fireEvent.focus(box(name));
  if (text !== undefined) fireEvent.change(box(name), { target: { value: text } });
};

beforeEach(() => {
  suggestCode.mockResolvedValue({ ok: true, code: "R03" });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PlacePicker", () => {
  it("shows only the search and Site until something is picked, then one level more at a time", () => {
    const { unmount } = render(<Harness />);
    expect(box("Find a place")).toBeTruthy();
    expect(box("Site")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Location" })).toBeNull();
    expect(screen.getByText("not set")).toBeTruthy();
    unmount();

    render(<Harness start="DIFC" />);
    expect(box("Location")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Building" })).toBeNull();
  });

  it("finds a room by two words and a pick sets the full ID", () => {
    render(<Harness />);
    open("Find a place", "gv4 r02");
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toHaveLength(2);
    expect(options[0]).toContain("Room 2");
    expect(options[1]).toContain("Rack 1");
    fireEvent.click(screen.getByRole("option", { name: /Rack 1/ }));
    expect(onChange).toHaveBeenLastCalledWith(`${FLOOR}-R02-RK01`);
    // every level above is filled, down to the rack
    expect(box("Rack")).toBeTruthy();
    expect((box("Building") as HTMLInputElement).value).toBe("Gate Village Building 4");
  });

  it("offers create for typed text with no match and still lists what exists", () => {
    render(<Harness start={FLOOR} />);
    open("Room", "Comms room");
    expect(screen.getByText(/Nothing in Level 1 is called/)).toBeTruthy();
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options[0]).toBe('+ Create room “Comms room”');
    expect(options.join(" ")).toContain("Room 1");
    expect(options.join(" ")).toContain("Room 2");
  });

  it("creates a room under the picked parent, shows the parents as fixed, and selects the result", async () => {
    const room = place(`${FLOOR}-R03`, "Comms room");
    createPlace.mockResolvedValue({ ok: true, place: room, created: [room] });
    render(<Harness start={FLOOR} />);
    open("Room", "Comms room");
    fireEvent.click(screen.getByRole("option", { name: /Create room/ }));
    const form = screen.getByRole("group", { name: "New room" });
    const inside = within(form).getByLabelText("Inside");
    expect(inside.textContent).toContain("Gate Avenue");
    expect(inside.textContent).toContain("Level 1");
    await waitFor(() => expect((within(form).getByLabelText("Code") as HTMLInputElement).value).toBe("R03"));
    expect(within(form).getByText(`${FLOOR}-R03`)).toBeTruthy();
    fireEvent.change(within(form).getByLabelText("Room type"), { target: { value: "comms_room" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create room" }));

    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(`${FLOOR}-R03`));
    expect(createPlace).toHaveBeenCalledWith({
      parent_id: FLOOR,
      name: "Comms room",
      code: "R03",
      room_type: "comms_room",
    });
    expect(onCreated).toHaveBeenCalledWith(room);
  });

  it("suggests the code while the name is typed, keeps a typed code, and refuses a code that is taken", async () => {
    render(<Harness start={FLOOR} />);
    open("Room", "Comms room");
    fireEvent.click(screen.getByRole("option", { name: /Create room/ }));
    const form = screen.getByRole("group", { name: "New room" });
    suggestCode.mockResolvedValue({ ok: true, code: "R04" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Store" } }); // no blur
    await waitFor(() => expect((within(form).getByLabelText("Code") as HTMLInputElement).value).toBe("R04"));
    fireEvent.change(within(form).getByLabelText("Code"), { target: { value: "r 02" } }); // R02 exists on this floor
    expect((within(form).getByLabelText("Code") as HTMLInputElement).value).toBe("R02");
    expect((await within(form).findByRole("alert")).textContent).toContain("Already used by");
    expect((within(form).getByRole("button", { name: "Create room" }) as HTMLButtonElement).disabled).toBe(true);
    expect(createPlace).not.toHaveBeenCalled();
  });

  it("lets an administrator create a location, with the site fixed", async () => {
    const ga = place("DIFC-GC", "Gate City");
    createPlace.mockResolvedValue({ ok: true, place: ga, created: [ga] });
    suggestCode.mockResolvedValue({ ok: true, code: "GC" });
    render(<Harness start="DIFC" />);
    open("Location", "Gate City");
    fireEvent.click(screen.getByRole("option", { name: /Create location/ }));
    await waitFor(() => expect((screen.getByLabelText("Code") as HTMLInputElement).value).toBe("GC"));
    fireEvent.click(screen.getByRole("button", { name: "Create location" }));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("DIFC-GC"));
    expect(createPlace).toHaveBeenCalledWith({ parent_id: "DIFC", name: "Gate City", code: "GC" });
  });

  it("ends the site, location and building lists with the administrator line when upper creation is not allowed", () => {
    render(<Harness start="DIFC" canCreate={{ upper: false, lower: true }} />);
    open("Location", "Gate District");
    expect(screen.getByText(/added by an administrator/)).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Create location/ })).toBeNull();
    // the lower levels still offer create
    cleanup();
    render(<Harness start={FLOOR} canCreate={{ upper: false, lower: true }} />);
    open("Room", "Comms");
    expect(screen.getByRole("option", { name: /Create room/ })).toBeTruthy();
  });

  it("clears the levels below when another location is picked", () => {
    render(<Harness start={`${FLOOR}-R02-RK01`} />);
    open("Location");
    fireEvent.click(screen.getByRole("option", { name: /Gate Boulevard/ }));
    expect(onChange).toHaveBeenLastCalledWith("DIFC-GB");
    expect(screen.queryByRole("combobox", { name: "Floor" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Rack" })).toBeNull();
  });

  it("fills a lone location, building and floor with the 'only one' mark and does not pick a rack", () => {
    render(<Harness />);
    open("Site");
    fireEvent.click(screen.getByRole("option", { name: /Jumeirah/ }));
    expect(onChange).toHaveBeenLastCalledWith("JBR-M-M-GF-R01");
    expect(screen.getAllByText("only one").length).toBe(4);
    const rack = box("Rack") as HTMLInputElement;
    expect(rack.value).toBe("");
  });

  it("sends a chain for a small site and shows what will be created", async () => {
    const site = place("BRN", "Branch");
    createPlace.mockResolvedValue({ ok: true, place: site, created: [site, place("BRN-M", "Main"), place("BRN-M-M", "Main")] });
    suggestCode.mockResolvedValue({ ok: true, code: "BRN" });
    render(<Harness />);
    open("Site", "Branch");
    fireEvent.click(screen.getByRole("option", { name: /Create site/ }));
    await waitFor(() => expect((screen.getByLabelText("Code") as HTMLInputElement).value).toBe("BRN"));
    fireEvent.click(screen.getByLabelText(/Small site/));
    expect(screen.getByText("BRN-M-M")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create site" }));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("BRN-M-M"));
    expect(createPlace).toHaveBeenCalledWith({
      parent_id: null,
      name: "Branch",
      code: "BRN",
      address: "",
      contact: "",
      chain: [
        { name: "Main", code: "M" },
        { name: "Main", code: "M" },
      ],
    });
  });

  it("shows 'Use it' when the code is taken, and picking it selects the existing place", async () => {
    const existing = PLACES.find((p) => p.id === `${FLOOR}-R01`)!;
    createPlace.mockResolvedValue({ ok: false, error: "R01 is already Room 1 (DIFC-GA-GV4-L1-R01).", existing });
    suggestCode.mockResolvedValue({ ok: true, code: "R01" });
    render(<Harness start={FLOOR} />);
    open("Room", "Store");
    fireEvent.click(screen.getByRole("option", { name: /Create room/ }));
    await waitFor(() => expect((screen.getByLabelText("Code") as HTMLInputElement).value).toBe("R01"));
    fireEvent.click(screen.getByRole("button", { name: "Create room" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use it" }));
    expect(onChange).toHaveBeenLastCalledWith(`${FLOOR}-R01`);
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("works from the keyboard: arrows move, Enter picks, Escape closes", () => {
    render(<Harness start={FLOOR} />);
    const room = box("Room");
    fireEvent.keyDown(room, { key: "ArrowDown" });
    expect(room.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(room, { key: "ArrowDown" });
    expect(screen.getAllByRole("option")[1]!.getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(room, { key: "ArrowUp" });
    fireEvent.keyDown(room, { key: "ArrowDown" });
    fireEvent.keyDown(room, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith(`${FLOOR}-R02`);
    expect(box("Room").getAttribute("aria-expanded")).toBe("false");

    fireEvent.keyDown(box("Find a place"), { key: "ArrowDown" });
    fireEvent.change(box("Find a place"), { target: { value: "comms" } });
    expect(screen.getAllByRole("option").length).toBe(2);
    fireEvent.keyDown(box("Find a place"), { key: "Escape" });
    expect(screen.queryAllByRole("option").length).toBe(0);
  });

  it("does not list an inactive place, and marks a value that sits in one", () => {
    const places = PLACES.map((p) => (p.id === `${FLOOR}-R01` ? { ...p, active: false } : p));
    render(
      <PlacePicker value={`${FLOOR}-R01`} onChange={onChange} places={places} canCreate={ADMIN} onCreated={onCreated} />,
    );
    expect(screen.getByText("inactive")).toBeTruthy();
    open("Room");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).not.toContain("R01 Room 1");
  });
});

describe("PlacePath", () => {
  it("shows the names above and the ID", () => {
    render(<PlacePath id="DIFC-GA-GV4" places={PLACES} />);
    expect(screen.getByText("Gate Village Building 4")).toBeTruthy();
    expect(screen.getByText("DIFC-GA-GV4")).toBeTruthy();
  });
});
