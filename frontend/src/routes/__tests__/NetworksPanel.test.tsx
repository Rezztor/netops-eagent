// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as networkApi from "@/api/networkConsole";
import type { ApplianceNetwork, NetworkList } from "@/api/networkConsole";
import { NetworksPanel } from "../NetworksPanel";

vi.mock("@/api/networkConsole", () => ({ getNetworks: vi.fn() }));
const getNetworks = vi.mocked(networkApi.getNetworks);

const net = (extra: Partial<ApplianceNetwork>): ApplianceNetwork => ({
  name: "VLAN 100", roles: ["devices"], interface: "vlan100", parent: "eno1", vlan: 100,
  address: "10.0.100.5/24", gateway: null, subnet: "10.0.100.0/24", routed_subnets: [], state: "up", ...extra,
});

const reply = (extra: Partial<NetworkList>): NetworkList => ({
  ok: true, known: true, generated_at: 1, networks: [], hint: "", labels: {}, ...extra,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("NetworksPanel", () => {
  it("lists each network with what it reaches and says where networks are changed", async () => {
    getNetworks.mockResolvedValue(
      reply({
        networks: [
          net({}),
          net({ name: "VLAN 200", interface: "vlan200", address: "10.0.200.5/24", gateway: "10.0.200.1", subnet: "10.0.200.0/24", routed_subnets: ["10.20.0.0/16"], state: "down" }),
        ],
        hint: "On your devices, allow management from 10.0.100.5 (VLAN 100), 10.0.200.5 (VLAN 200).",
      }),
    );
    render(<NetworksPanel />);
    const row = (await screen.findByText("VLAN 200")).closest("tr")!;
    for (const text of ["devices", "vlan200", "10.0.200.5/24", "10.0.200.1", "10.0.200.0/24", "10.20.0.0/16", "down"]) {
      expect(within(row).getByText(text)).toBeTruthy();
    }
    expect(screen.getByText(/allow management from 10\.0\.100\.5 \(VLAN 100\)/)).toBeTruthy();
    expect(screen.getByRole("note").textContent).toContain("appliance console, menu Network");
  });

  it("says plainly that nothing is known when there is no list", async () => {
    getNetworks.mockResolvedValue(reply({ known: false, generated_at: null }));
    render(<NetworksPanel />);
    await screen.findByText("One network");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("note").textContent).toContain("menu Network");
  });
});
