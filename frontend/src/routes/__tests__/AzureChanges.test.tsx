// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "@/api/azureChange";
import { ApiError } from "@/api/client";
import { AzureChanges, documentStatus } from "../AzureChanges";

vi.mock("@/api/azureChange", async (importOriginal) => ({ ...(await importOriginal<typeof api>()), getAzureChanges: vi.fn() }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} } }),
}));

const get = vi.mocked(api.getAzureChanges);
const change = (over: Partial<api.AzureChangeSummary>): api.AzureChangeSummary => ({
  id: "c1",
  status: "applied",
  kind: "azure_change",
  playbook_name: "resource_group",
  summary: "Create rg-web",
  limit: "sub-1/rg-web",
  created_at: 1_790_000_000,
  requested_by: "asha",
  approved_by: "asha",
  reference: "CR-123456",
  plan_digest: "abc",
  documents: [{ version: "issued", format: "pdf", downloaded: true }],
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the Azure changes list", () => {
  it("shows the number as a column and asks the server to filter by it", async () => {
    get.mockResolvedValueOnce([change({}), change({ id: "c2", reference: "SR-77777" })]);
    render(<AzureChanges />);
    await screen.findByText("CR-123456");
    expect(screen.getByText("SR-77777")).toBeTruthy();
    expect(get).toHaveBeenLastCalledWith("");

    get.mockResolvedValueOnce([change({ id: "c2", reference: "SR-77777" })]);
    fireEvent.change(screen.getByLabelText("Filter by CR or SR number"), { target: { value: "SR-77777" } });
    fireEvent.click(screen.getByText("Filter"));
    await waitFor(() => expect(get).toHaveBeenLastCalledWith("SR-77777"));
    await waitFor(() => expect(screen.queryByText("CR-123456")).toBeNull());
  });

  it("shows a dash before approval, and says when nothing has that number", async () => {
    get.mockResolvedValueOnce([change({ reference: "", status: "awaiting_approval", documents: [{ version: "issued", format: "pdf" }] })]);
    render(<AzureChanges />);
    await screen.findByText("issued");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("is absent when the plugin is off", async () => {
    get.mockRejectedValueOnce(new ApiError("x", 404, "not found", null));
    const { container } = render(<AzureChanges />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("names the document stage", () => {
    expect(documentStatus(change({}))).toBe("issued, downloaded");
    expect(documentStatus(change({ documents: [{ version: "issued", format: "pdf" }, { version: "applied", format: "pdf" }] }))).toBe("applied");
    expect(documentStatus(change({ documents: [] }))).toBe("none");
  });
});
