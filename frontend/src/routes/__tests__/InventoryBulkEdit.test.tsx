// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as bulkApi from "@/api/inventoryBulk";
import * as placesApi from "@/api/inventoryPlaces";
import { ToastProvider } from "@/components";
import { BulkEditDialog } from "../InventoryBulkEdit";

vi.mock("@/api/inventoryBulk", () => ({ proposeMany: vi.fn() }));
vi.mock("@/api/inventoryPlaces", async (importOriginal) => ({
  ...(await importOriginal<typeof placesApi>()),
  listPlaces: vi.fn(),
}));
const proposeMany = vi.mocked(bulkApi.proposeMany);
const listPlaces = vi.mocked(placesApi.listPlaces);

const rack = (id: string, name: string): placesApi.Place => {
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
};
const PLACES = [
  rack("HQ", "HQ"),
  rack("HQ-M", "Main"),
  rack("HQ-M-M", "Main"),
  rack("HQ-M-M-L1", "Level 1"),
  rack("HQ-M-M-L1-R01", "Comms"),
  rack("HQ-M-M-L1-R01-RK07", "Rack 7"),
];

const devices = [
  { id: "d1", name: "sw-a" },
  { id: "d2", name: "sw-b" },
];

function open() {
  const onClose = vi.fn();
  const onDone = vi.fn();
  render(
    <ToastProvider>
      <BulkEditDialog devices={devices} onClose={onClose} onDone={onDone} />
    </ToastProvider>,
  );
  return { onClose, onDone };
}

beforeEach(() => {
  listPlaces.mockResolvedValue({ ok: true, places: PLACES, room_types: [] });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("BulkEditDialog", () => {
  it("shows the count and the accountability copy, and sends only the ticked fields", async () => {
    proposeMany.mockResolvedValue({ ok: true, batch_id: "b1", created: 2, unchanged: 0, failed: [] });
    const { onDone } = open();
    expect(screen.getByTestId("bulk-count").textContent).toContain("2 devices selected");
    expect(screen.getByText(/makes you accountable for it/)).toBeTruthy();
    const submit = screen.getByRole("button", { name: "Send 2 changes to Review" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.click(screen.getByLabelText("Change Rack unit"));
    fireEvent.change(screen.getByLabelText("Rack unit"), { target: { value: "22" } });
    fireEvent.click(screen.getByLabelText("Change Warranty expiry"));
    fireEvent.change(screen.getByLabelText("Warranty expiry"), { target: { value: "2027-03-01" } });
    fireEvent.click(screen.getByLabelText("Change Place"));
    await waitFor(() => expect(listPlaces).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Find a place"), { target: { value: "rack 7" } });
    fireEvent.click(await screen.findByRole("option", { name: /Rack 7/ }));
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "move" } });
    fireEvent.click(submit);

    await waitFor(() => expect(onDone).toHaveBeenCalledWith("b1"));
    expect(proposeMany).toHaveBeenCalledWith(
      ["d1", "d2"],
      { rack_unit: 22, warranty_expiry: "2027-03-01", place_id: "HQ-M-M-L1-R01-RK07" },
      "move",
    );
  });

  it("adds a tag to each device's own tags, one batch per distinct tag set", async () => {
    proposeMany.mockResolvedValue({ ok: true, batch_id: "b3", created: 1, unchanged: 0, failed: [] });
    const onDone = vi.fn();
    render(
      <ToastProvider>
        <BulkEditDialog
          devices={[{ id: "d1", name: "sw-a", tags: ["lab"] }, { id: "d2", name: "sw-b", tags: ["core"] }]}
          only={["tags"]}
          onClose={vi.fn()}
          onDone={onDone}
        />
      </ToastProvider>,
    );
    const send = screen.getByRole("button", { name: "Send 2 changes to Review" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Tag to add"), { target: { value: "Edge" } });
    fireEvent.click(send);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("b3"));
    expect(proposeMany).toHaveBeenCalledWith(["d1"], { tags: ["edge", "lab"] }, undefined);
    expect(proposeMany).toHaveBeenCalledWith(["d2"], { tags: ["core", "edge"] }, undefined);
  });

  it("clears a ticked field left empty and stays open when nothing was proposed", async () => {
    proposeMany.mockResolvedValue({ ok: true, batch_id: "b2", created: 0, unchanged: 2, failed: [] });
    const { onDone } = open();
    fireEvent.click(screen.getByLabelText("Change End of life"));
    fireEvent.click(screen.getByRole("button", { name: "Send 2 changes to Review" }));
    await waitFor(() => expect(proposeMany).toHaveBeenCalledWith(["d1", "d2"], { eol_date: null }, undefined));
    expect(onDone).not.toHaveBeenCalled();
  });
});
