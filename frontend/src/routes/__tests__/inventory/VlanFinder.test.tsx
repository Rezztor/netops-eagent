// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as portsApi from "@/api/inventoryPorts";
import { ToastProvider } from "@/components";
import { VlanFinder } from "../../inventory/VlanFinder";

vi.mock("@/api/inventoryPorts", async (importOriginal) => ({ ...(await importOriginal<typeof portsApi>()), getVlan: vi.fn() }));
const getVlan = vi.mocked(portsApi.getVlan);

afterEach(cleanup);

describe("VlanFinder", () => {
  it("looks a VLAN up and links each device to its record", async () => {
    getVlan.mockResolvedValue({ ok: true, vlan: 100, ports: [{ device_id: "d1", device: "lab-sonic", port: "Ethernet4", mode: "access" }] });
    const open = vi.fn();
    render(
      <ToastProvider>
        <VlanFinder onClose={() => {}} onOpenRecord={open} />
      </ToastProvider>,
    );
    fireEvent.change(screen.getByLabelText("VLAN number"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Find" }));
    fireEvent.click(await screen.findByRole("button", { name: "lab-sonic" }));
    expect(getVlan).toHaveBeenCalledWith(100);
    expect(screen.getByText("Ethernet4")).toBeTruthy();
    expect(open).toHaveBeenCalledWith("d1");
  });

  it("says so when no port carries it", async () => {
    getVlan.mockResolvedValue({ ok: true, vlan: 7, ports: [] });
    render(
      <ToastProvider>
        <VlanFinder onClose={() => {}} onOpenRecord={() => {}} />
      </ToastProvider>,
    );
    fireEvent.change(screen.getByLabelText("VLAN number"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Find" }));
    await waitFor(() => expect(screen.getByText("No stored port carries VLAN 7.")).toBeTruthy());
  });
});
