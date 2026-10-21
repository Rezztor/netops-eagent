// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as inventoryApi from "@/api/inventory";
import type { InventoryBatch, InventoryConflict, Proposal } from "@/api/inventory";
import type * as placesApi from "@/api/inventoryPlaces";
import { ConfirmProvider, ToastProvider } from "@/components";
import { ReviewPanel, SELF_APPROVAL_BATCH_COPY, SELF_APPROVAL_COPY } from "../../inventory/ReviewPanel";

vi.mock("@/api/inventory", async (importOriginal) => ({
  ...(await importOriginal<typeof inventoryApi>()),
  listProposals: vi.fn(),
  listBatches: vi.fn(),
  decideProposal: vi.fn(),
  decideBatch: vi.fn(),
  listConflicts: vi.fn(),
  listPins: vi.fn(),
  decideConflict: vi.fn(),
  decideConflicts: vi.fn(),
  unpinField: vi.fn(),
}));
vi.mock("@/api/inventoryPlaces", async (importOriginal) => ({
  ...(await importOriginal<typeof placesApi>()),
  listPlaces: vi.fn().mockResolvedValue({ ok: true, places: [], room_types: [] }),
}));
vi.mock("@/lib/session", () => ({ useSession: () => session }));

let session = { user: { id: "user-asha", username: "asha" }, preferences: { timezone: "UTC", time_format: "24h" } };
const m = vi.mocked(inventoryApi);

const proposal = (over: Partial<Proposal>): Proposal => ({
  id: "prop-1", kind: "update", device_id: "dev-1", device_name: "lab-ios", base_version: 1,
  changes: { role: { old: "access", new: "core" } }, source: "manual", evidence: null, status: "pending",
  requested_by: "asha", requested_at: "2026-01-03T00:00:00Z", decided_by: null, decided_at: null, decision_note: null,
  ...over,
});
const ev = (quote: string, location: string, confidence: number) => ({
  file: "devices.xlsx", quote, location, confidence, batch_id: "batch-1",
});
const manual = proposal({});
const extA = proposal({ id: "a", kind: "create", device_id: null, device_name: "sw-a", changes: { name: { old: null, new: "sw-a" } }, source: "extraction", evidence: ev("sw-a,Cisco", "row 2", 0.95) });
const extB = proposal({ id: "b", kind: "create", device_id: null, device_name: "sw-b", changes: { name: { old: null, new: "sw-b" } }, source: "extraction", evidence: ev("sw-b,Arista", "row 3", 0.6) });
const batch: InventoryBatch = {
  batch_id: "batch-1", file: "devices.xlsx", source: "extraction", requested_by: "asha",
  requested_at: "2026-01-04T00:00:00Z", counts: { pending: 2, approved: 0, rejected: 0, superseded: 0 }, total: 2,
};
const conflict = (id: string, field: string): InventoryConflict => ({
  id, device_id: "dev-1", device_name: "fw-a", field, current_value: "OLD", current_source: "manual",
  proposed_value: "NEW", source: "facts", observed_at: "2026-01-05T00:00:00Z", status: "open",
  decided_by: null, decided_at: null, note: null, created_at: "2026-01-05T00:00:00Z",
});

function renderPanel(url = "/inventory?tab=review", canApprove = true) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <ToastProvider>
        <ConfirmProvider>
          <ReviewPanel canApprove={canApprove} onChanged={() => {}} />
        </ConfirmProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}
const click = (el: HTMLElement) => act(async () => void fireEvent.click(el));

beforeEach(() => {
  session = { ...session, user: { id: "user-asha", username: "asha" } };
  m.listProposals.mockResolvedValue({ ok: true, proposals: [extA, extB, manual], total: 3, pending_count: 3 });
  m.listBatches.mockResolvedValue({ ok: true, batches: [batch], total: 1 });
  m.decideProposal.mockResolvedValue({ ok: true });
  m.decideBatch.mockResolvedValue({ ok: true, decided: 2 });
  m.listConflicts.mockResolvedValue({ ok: true, conflicts: [conflict("c1", "model"), conflict("c2", "serial")], total: 2, open_count: 2 });
  m.listPins.mockResolvedValue({
    ok: true,
    pins: [{ device_id: "dev-1", device_name: "fw-a", field: "vendor", source: "facts", pinned_by: "asha", pinned_at: "2026-01-05T00:00:00Z" }],
  });
  m.decideConflict.mockResolvedValue({ ok: true, conflict: conflict("c1", "model") });
  m.decideConflicts.mockResolvedValue({ ok: true, decided: 2, failed: [] });
  m.unpinField.mockResolvedValue({ ok: true });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Pending changes", () => {
  it("lists the groups and shows the first batch with old to new and evidence", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("batch-batch-1")).toBeTruthy());
    expect(screen.getByTestId("group-device:dev-1").textContent).toMatch(/lab-ios/);
    const detail = screen.getByTestId("batch-batch-1");
    expect(within(detail).getByText("devices.xlsx")).toBeTruthy();
    expect(within(detail).getByText(/Approve all \(2\)/)).toBeTruthy();
    expect(screen.getByTestId("evidence-a").textContent).toMatch(/row 2.*sw-a,Cisco|sw-a,Cisco/);
    expect(screen.getByTestId("confidence-a").textContent).toMatch(/0\.95/);
    fireEvent.click(screen.getByTestId("group-device:dev-1"));
    expect(screen.getByTestId("diff-prop-1").textContent).toMatch(/access.*to.*core/);
  });

  it("shows both paths when a place changes", async () => {
    const move = proposal({ id: "mv", changes: { place_id: { old: "LAB-M-LAB-L1-R101", new: "LAB-M-LAB-L1-R101-RK01" } } });
    m.listProposals.mockResolvedValue({ ok: true, proposals: [move], total: 1, pending_count: 1 });
    m.listBatches.mockResolvedValue({ ok: true, batches: [], total: 0 });
    renderPanel();
    const diff = await screen.findByTestId("diff-mv");
    await waitFor(() => expect(diff.textContent).toContain("LAB-M-LAB-L1-R101-RK01"));
    expect(diff.textContent).toContain("Place");
    expect(diff.textContent).toMatch(/LAB-M-LAB-L1-R101.*to.*LAB-M-LAB-L1-R101-RK01/);
  });

  it("shows the accountability copy to the requester and neutral copy to others", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText(SELF_APPROVAL_BATCH_COPY)).toBeTruthy());
    fireEvent.click(screen.getByTestId("group-device:dev-1"));
    await click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getAllByText(SELF_APPROVAL_COPY).length).toBeGreaterThan(0));
    cleanup();
    session = { ...session, user: { id: "user-ravi", username: "ravi" } };
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("batch-batch-1")).toBeTruthy());
    expect(screen.queryByText(SELF_APPROVAL_BATCH_COPY)).toBeNull();
  });

  it("decides a single proposal with the edit override", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("batch-batch-1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("group-device:dev-1"));
    await click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.change(screen.getByLabelText("Edit role"), { target: { value: "edge" } });
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(m.decideProposal).toHaveBeenCalledWith(expect.objectContaining({ id: "prop-1", decision: "approve", edits: { role: "edge" } })),
    );
  });

  it("approves and rejects the whole batch, with min_confidence only when asked", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText(/Approve all \(2\)/)).toBeTruthy());
    fireEvent.click(screen.getByLabelText(/Only confidence/i));
    await click(screen.getByRole("button", { name: /Approve all \(2\)/ }));
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: /Approve all \(2\)/ }));
    await waitFor(() =>
      expect(m.decideBatch).toHaveBeenCalledWith({ batch_id: "batch-1", decision: "approve", min_confidence: 0.8 }),
    );
    fireEvent.click(screen.getByLabelText(/Only confidence/i));
    await click(screen.getByRole("button", { name: "Reject all" }));
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reject all" }));
    await waitFor(() => expect(m.decideBatch).toHaveBeenLastCalledWith({ batch_id: "batch-1", decision: "reject" }));
  });

  it("hides decisions without inventory.approve", async () => {
    renderPanel("/inventory?tab=review", false);
    await waitFor(() => expect(screen.getByTestId("batch-batch-1")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Approve all/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("shows an empty state", async () => {
    m.listProposals.mockResolvedValue({ ok: true, proposals: [], total: 0, pending_count: 0 });
    m.listBatches.mockResolvedValue({ ok: true, batches: [], total: 0 });
    renderPanel();
    await waitFor(() => expect(screen.getByText("Nothing waiting")).toBeTruthy());
  });
});

describe("Conflicts", () => {
  it("switches queue, shows current vs observed and the counts", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByRole("button", { name: /Conflicts\s*2/ })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Conflicts/ }));
    await waitFor(() => expect(screen.getByTestId("conflict-row-c1")).toBeTruthy());
    const row = screen.getByTestId("conflict-row-c1");
    expect(row.textContent).toContain("OLD (manual)");
    expect(row.textContent).toContain("NEW (facts");
    expect(screen.getByText("Pinned fields")).toBeTruthy();
  });

  it("decides one conflict, bulk-accepts and unpins", async () => {
    renderPanel("/inventory?tab=review&queue=conflicts");
    await waitFor(() => expect(screen.getByTestId("conflict-row-c1")).toBeTruthy());
    await click(within(screen.getByTestId("conflict-row-c1")).getByRole("button", { name: "Pin current" }));
    expect(m.decideConflict).toHaveBeenCalledWith({ id: "c1", decision: "pin" });
    fireEvent.click(screen.getByLabelText("Select all conflicts"));
    fireEvent.click(screen.getByTestId("bulk-accept"));
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: "Accept observed" }));
    await waitFor(() => expect(m.decideConflicts).toHaveBeenCalledWith({ ids: ["c1", "c2"], decision: "accept" }));
    await click(screen.getByRole("button", { name: "Unpin" }));
    expect(m.unpinField).toHaveBeenCalledWith({ device_id: "dev-1", field: "vendor", source: "facts" });
  });

  it("hides decisions without inventory.approve and shows empty states", async () => {
    renderPanel("/inventory?tab=review&queue=conflicts", false);
    await waitFor(() => expect(screen.getByTestId("conflict-row-c1")).toBeTruthy());
    for (const name of ["Accept observed", "Keep current", "Unpin"]) expect(screen.queryByRole("button", { name })).toBeNull();
    expect(screen.queryByLabelText("Select all conflicts")).toBeNull();
    cleanup();
    m.listConflicts.mockResolvedValue({ ok: true, conflicts: [], total: 0, open_count: 0 });
    m.listPins.mockResolvedValue({ ok: true, pins: [] });
    renderPanel("/inventory?tab=review&queue=conflicts");
    await waitFor(() => expect(screen.getByText("No open conflicts")).toBeTruthy());
    expect(screen.getByText("No pinned fields.")).toBeTruthy();
  });
});
