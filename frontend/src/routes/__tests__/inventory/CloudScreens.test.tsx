// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as azure from "@/api/azure";
import * as summaryApi from "@/api/inventorySummary";
import { ConfirmProvider, ToastProvider } from "@/components";
import { InventoryScreen } from "../../InventoryScreen";
import { CloudInventory } from "../../inventory/cloud/CloudInventory";

vi.mock("@/api/azure", async (importOriginal) => ({
  ...(await importOriginal<typeof azure>()),
  getAzureStatus: vi.fn(),
  getAzureTree: vi.fn(),
  listAzureResources: vi.fn(),
  getAzureResource: vi.fn(),
  getAzureWaste: vi.fn(),
}));
vi.mock("@/api/inventorySummary", () => ({ getInventorySummary: vi.fn() }));
vi.mock("../../inventory/DevicesPanel", () => ({ DevicesPanel: () => <div>devices-panel</div> }));
// The Azure tab's other two panels belong to another screen's tests.
vi.mock("../../inventory/cloud/AzureHistory", () => ({ AzureHistory: () => <div>history-panel</div> }));
vi.mock("../../inventory/cloud/AzureSources", () => ({ AzureSources: () => <div>sources-panel</div> }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    permissions: ["inventory.view", "inventory.manage"],
    preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} },
  }),
}));

const m = {
  status: vi.mocked(azure.getAzureStatus),
  tree: vi.mocked(azure.getAzureTree),
  list: vi.mocked(azure.listAzureResources),
  one: vi.mocked(azure.getAzureResource),
  waste: vi.mocked(azure.getAzureWaste),
};

const PULL = "2026-09-26T02:00:00Z";
const run = { id: "run1", status: "succeeded", started_at: PULL, finished_at: "2026-09-26T02:00:41Z", counts: null, error: null, requested_by: "system:schedule" } as azure.AzureRun;
const asOf: azure.AzureAsOf = {
  last_pull: run,
  subscriptions: [
    { name: "Production", last_read_at: "2026-09-26T02:01:00Z", readable: true },
    { name: "Sandbox", last_read_at: "2026-09-19T02:01:00Z", readable: false },
  ],
};
const status = (over: Partial<azure.AzureStatus> = {}): azure.AzureStatus => ({
  ok: true, enabled: true, configured: true, has_data: true, test_endpoints: false, running: null, last_run: run,
  next_pull_at: null, as_of: asOf, credential: { kind: "certificate", expires_on: "", expires_in_days: 300 },
  verified_against_real_tenant: false, ...over,
});

const node = (id: string, name: string, count: number, categories: azure.TreeNode["categories"], over: Partial<azure.TreeNode> = {}): azure.TreeNode => ({
  id, native_id: `/${id}`, name, state: "Enabled", region: "uaenorth", tags: { env: "prod" }, props: {}, readable: true,
  last_read_at: "2026-09-26T02:01:00Z", count, categories, ...over,
});
const TREE: azure.AzureTree = {
  ok: true, enabled: true, as_of: asOf,
  attention: { stopped_failed: 1, no_tags: 2, exposed: 3, changed: 4, waste: 5, drifted: 0 },
  tree: {
    account: { id: "a", native_id: "t", name: "example.onmicrosoft.com" },
    subscriptions: [
      { ...node("s1", "Production", 10, { web: 6, networking: 4 }), groups: [node("g1", "rg-portal", 6, { web: 6 }), node("g2", "rg-network", 4, { networking: 4 })] },
      { ...node("s2", "Sandbox", 2, { other: 2 }, { readable: false, last_read_at: "2026-09-19T02:01:00Z" }), groups: [] },
    ],
  },
};

const res = (id: string, name: string, over: Partial<azure.CloudResource> = {}): azure.CloudResource => ({
  id, native_id: `/subscriptions/x/${id}`, name, type: "microsoft.web/sites", kind: "app", label: "Web app", category: "web",
  subscription: { id: "s1", name: "Production" }, resource_group: { id: "g1", name: "rg-portal" }, parent: null, region: "uaenorth",
  state: "running", state_detail: "", sku: {}, tags: { env: "prod" }, props: {}, first_seen_at: "2026-08-01T00:00:00Z",
  last_seen_at: PULL, changed_at: null, removed_at: null, ...over,
});
const ROWS = [
  res("r1", "app-portal-prod"),
  res("r2", "app-portal-jobs", { label: "Function app", state: "stopped" }),
  res("r3", "app-old", { removed_at: "2026-09-26T02:00:00Z" }),
];
const FACETS = { types: [{ type: "microsoft.web/sites", label: "Web app", count: 2 }], regions: ["uaenorth"], states: ["running", "stopped"], tag_names: ["env"] };

const link = (kind: string, id: string | null, name: string, label = ""): azure.CloudLink => ({
  kind, to_native_id: `/x/${name}`, props: {}, id, name, type: null, label, state: null,
});
const page = (resource: azure.CloudResource, over: Partial<azure.ResourcePage> = {}): azure.ResourcePage => ({
  ok: true, resource, links_out: [], links_in: [], children: [], changes: [], exposure: [], portal_url: "https://portal.azure.com/x", ...over,
});

function Where() {
  return <output data-testid="where">{useLocation().search}</output>;
}
const where = () => screen.getByTestId("where").textContent ?? "";

function renderCloud(url = "/inventory?scope=cloud", s = status()) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <ToastProvider>
        <ConfirmProvider>
          <CloudInventory status={s} canManage onChanged={() => {}} />
          <Where />
        </ConfirmProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  m.status.mockResolvedValue(status());
  m.tree.mockResolvedValue(TREE);
  m.list.mockImplementation(async (p) => {
    const rows = ROWS.filter((r) => (p.include_removed ? true : !r.removed_at) && (!p.state || r.state === p.state));
    return { ok: true, resources: rows, total: rows.length, facets: FACETS };
  });
  vi.mocked(summaryApi.getInventorySummary).mockResolvedValue({
    ok: true, devices: 5, sites: 1, pending: 0, conflicts: 0, lifecycle: { expired: 0, soon: 0 }, incomplete: 0, average: 100, stale: { count: 0, names: [] },
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the On-prem / Cloud switch", () => {
  const screenAt = (url: string) =>
    render(
      <MemoryRouter initialEntries={[url]}>
        <ToastProvider>
          <ConfirmProvider>
            <InventoryScreen />
            <Where />
          </ConfirmProvider>
        </ToastProvider>
      </MemoryRouter>,
    );

  it("is absent when the plugin was never enabled and has no data", async () => {
    m.status.mockResolvedValue(status({ enabled: false, has_data: false }));
    screenAt("/inventory");
    await screen.findByText("devices-panel");
    await waitFor(() => expect(m.status).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Cloud" })).toBeNull();
  });

  it("is absent when the status cannot be read, and the on-prem screen still shows", async () => {
    m.status.mockRejectedValue(new Error("403"));
    screenAt("/inventory?scope=cloud");
    await screen.findByText("devices-panel");
    expect(screen.queryByRole("button", { name: "Cloud" })).toBeNull();
  });

  it("is present when enabled, and puts scope=cloud in the address", async () => {
    screenAt("/inventory");
    fireEvent.click(await screen.findByRole("button", { name: "Cloud" }));
    expect(where()).toBe("?scope=cloud");
    expect(await screen.findByRole("tab", { name: "Azure" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "On-prem" }));
    expect(where()).toBe("");
    expect(await screen.findByText("devices-panel")).toBeTruthy();
  });

  it("is present when disabled but data is stored", async () => {
    m.status.mockResolvedValue(status({ enabled: false }));
    screenAt("/inventory");
    expect(await screen.findByRole("button", { name: "Cloud" })).toBeTruthy();
  });
});

describe("the Azure tab", () => {
  it("shows the counts at every level and a subscription it cannot read", async () => {
    renderCloud();
    const nav = await screen.findByRole("navigation", { name: "Azure resources" });
    expect(within(nav).getByRole("button", { name: /example\.onmicrosoft\.com\s*12/ })).toBeTruthy();
    expect(within(nav).getByRole("button", { name: /Production\s*10/ })).toBeTruthy();
    expect(within(nav).getByRole("button", { name: /rg-network\s*4/ })).toBeTruthy();
    expect(within(nav).getByText(/Not readable since/)).toBeTruthy();
  });

  it("says how old the data is and which subscription was not read in that pull", async () => {
    const { container } = renderCloud();
    await screen.findByRole("navigation", { name: "Azure resources" });
    expect(container.textContent).toMatch(/12 Azure resources in 2 subscriptions · as of/);
    expect(container.textContent).toMatch(/not verified against a real tenant/);
    expect(container.textContent).toMatch(/Not read in that pull: Sandbox \(as of/);
  });

  it("shows a notice and keeps the data when the plugin is disabled", async () => {
    renderCloud("/inventory?scope=cloud", status({ enabled: false }));
    expect(await screen.findByText("Azure is off. This is the picture from the last pull.")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Azure resources" })).toBeTruthy();
  });

  it("offers Set up when nothing is configured and nothing was pulled", async () => {
    renderCloud("/inventory?scope=cloud", status({ configured: false, has_data: false }));
    expect(await screen.findByText("Set up Azure")).toBeTruthy();
  });

  it("narrows by tile, node, category and filter, and writes each to the address", async () => {
    renderCloud();
    await screen.findByRole("navigation", { name: "Azure resources" });

    fireEvent.click(screen.getByRole("button", { name: /Stopped or failed/ }));
    expect(where()).toContain("attention=stopped_failed");
    await waitFor(() => expect(m.list).toHaveBeenLastCalledWith(expect.objectContaining({ attention: "stopped_failed" })));
    fireEvent.click(screen.getByRole("button", { name: /Stopped or failed/ }));
    expect(where()).not.toContain("attention");

    fireEvent.click(screen.getByRole("button", { name: /^Production\s*10/ }));
    expect(where()).toContain("node=s1");
    await waitFor(() => expect(m.list).toHaveBeenLastCalledWith(expect.objectContaining({ subscription: "s1" })));

    fireEvent.click(screen.getByRole("button", { name: /^rg-portal\s*6/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Web and apps\s*6/ }));
    expect(where()).toContain("node=g1");
    expect(where()).toContain("cat=web");
    await waitFor(() => expect(m.list).toHaveBeenLastCalledWith(expect.objectContaining({ resource_group: "g1", category: "web" })));

    fireEvent.change(await screen.findByLabelText("State"), { target: { value: "stopped" } });
    expect(where()).toContain("state=stopped");
    await waitFor(() => expect(screen.queryByText("app-portal-prod")).toBeNull());
    expect(screen.getByText("app-portal-jobs")).toBeTruthy();
  });

  it("shows what is possibly wasted with the rule, the evidence and a list price, and hides Drifted while it is zero", async () => {
    m.waste.mockResolvedValue({
      ok: true, as_of: PULL, total_usd_month: 21.5, priced: 1, unpriced: 1, priced_at: PULL,
      findings: [
        { rule: "unattached_disk", title: "Disk attached to nothing", id: "r1", native_id: "/x", name: "app-portal-prod", label: "Disk", subscription: "Production", resource_group: "rg-portal", region: "uaenorth", evidence: "disk state Unattached, 100 GB", as_of: PULL, usd_month: 21.5 },
        { rule: "unused_nsg", title: "Network security group on nothing", id: "r2", native_id: "/y", name: "app-portal-jobs", label: "NSG", subscription: "Production", resource_group: "rg-portal", region: "uaenorth", evidence: "no subnet and no network card uses it", as_of: PULL },
      ],
    });
    renderCloud();
    await screen.findByRole("navigation", { name: "Azure resources" });
    expect(screen.queryByRole("button", { name: /Drifted/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Possible waste/ }));
    expect(where()).toContain("attention=waste");
    await waitFor(() => expect(m.waste).toHaveBeenCalled());
    expect(await screen.findByText("Disk attached to nothing")).toBeTruthy();
    expect(screen.getByText("disk state Unattached, 100 GB")).toBeTruthy();
    expect(screen.getByText(/21\.50 USD a month \(list\)/)).toBeTruthy();
    expect(screen.getByText(/2 findings · about 21\.50 USD a month at list price for the 1 that have one/)).toBeTruthy();
    expect(screen.getByText(/a pointer to look, not a verdict/)).toBeTruthy();
  });

  it("shows the Drifted tile once a resource the product made has changed outside it, and lists those resources", async () => {
    m.tree.mockResolvedValue({ ...TREE, attention: { ...TREE.attention, drifted: 2 } });
    renderCloud();
    await screen.findByRole("navigation", { name: "Azure resources" });
    fireEvent.click(screen.getByRole("button", { name: /Drifted/ }));
    expect(where()).toContain("attention=drifted");
    await waitFor(() => expect(m.list).toHaveBeenLastCalledWith(expect.objectContaining({ attention: "drifted" })));
  });

  it("filters to the resources that lack a tag", async () => {
    renderCloud();
    await screen.findByText("app-portal-prod");
    fireEvent.change(await screen.findByLabelText("Missing tag"), { target: { value: "tag:env" } });
    expect(where()).toContain("without=tag%3Aenv");
    await waitFor(() => expect(m.list).toHaveBeenLastCalledWith(expect.objectContaining({ without: "tag:env" })));
  });

  it("opens a resource group by category and hides the empty categories", async () => {
    m.list.mockResolvedValue({ ok: true, resources: ROWS.slice(0, 2), total: 2, facets: FACETS });
    renderCloud("/inventory?scope=cloud&node=g1");
    expect(await screen.findByRole("heading", { name: /Web and apps 6/ })).toBeTruthy();
    expect(await screen.findByText("app-portal-prod")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Networking/ })).toBeNull();
    expect(screen.getByRole("button", { name: "4 more" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "4 more" }));
    expect(where()).toContain("cat=web");
  });

  it("greys removed resources and lists them only with the filter", async () => {
    renderCloud();
    await screen.findByText("app-portal-prod");
    expect(screen.queryByText("app-old")).toBeNull();
    fireEvent.click(screen.getByLabelText("Show removed"));
    expect(where()).toContain("removed=1");
    const row = (await screen.findByText("app-old")).closest("tr")!;
    expect(row.getAttribute("data-removed")).toBe("true");
    expect(within(row).getByText("removed")).toBeTruthy();
  });

  it("offers nothing to add, edit, review or delete", async () => {
    renderCloud();
    await screen.findByText("app-portal-prod");
    expect(screen.queryByRole("button", { name: /^(add|edit|review|delete|remove|approve)/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /^(add|edit)/i })).toBeNull();
  });

  it("switches tabs through ctab", async () => {
    renderCloud();
    fireEvent.click(await screen.findByRole("tab", { name: "History" }));
    expect(where()).toContain("ctab=history");
    expect(await screen.findByText("history-panel")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(await screen.findByText("sources-panel")).toBeTruthy();
  });
});

describe("a resource page", () => {
  it("shows a network with its subnets, the routes of the table that is there, and the peerings", async () => {
    const vnet = res("v1", "vnet-prod", {
      type: "microsoft.network/virtualnetworks", label: "Virtual network", category: "networking",
      props: {
        address_space: ["10.20.0.0/16"],
        peerings: [
          { name: "peer-shared", state: "Connected", remote_id: "/x/vnet-shared", remote_address_space: ["10.30.0.0/16"], allow_forwarded: true },
          { name: "peer-away", state: "Disconnected", remote_id: "/x/vnet-away", remote_address_space: ["10.60.0.0/16"] },
        ],
      },
    });
    const subnet = (id: string, name: string, over: Partial<azure.CloudResource> = {}) =>
      res(id, name, { type: "microsoft.network/virtualnetworks/subnets", label: "Subnet", category: "networking", props: { address_prefixes: ["10.20.1.0/24"] }, parent: { id: "v1", name: "vnet-prod" }, ...over });
    m.one.mockImplementation(async (id) => {
      if (id === "v1")
        return page(vnet, {
          children: [
            subnet("n1", "snet-apps", { links_out: [link("route_table", "rt1", "rt-prod-default"), link("nsg", "g9", "nsg-apps")] }),
            subnet("n2", "snet-mgmt", { links_out: [] }),
          ],
          links_out: [link("peering", "v2", "vnet-shared"), { ...link("peering", null, "vnet-away"), to_native_id: "/x/vnet-away" }],
        });
      return page(res("rt1", "rt-prod-default", { type: "microsoft.network/routetables", props: { bgp_propagation: true, routes: [{ name: "default-via-fw", prefix: "0.0.0.0/0", next_hop_type: "VirtualAppliance", next_hop_ip: "10.20.254.4" }] } }));
    });
    renderCloud("/inventory?scope=cloud&res=v1");
    expect(await screen.findByRole("heading", { name: "vnet-prod" })).toBeTruthy();
    const subnets = (await screen.findByRole("heading", { name: "Subnets" })).closest("section")!;
    expect(within(subnets.querySelectorAll("tr")[2] as HTMLElement).getByText("not configured")).toBeTruthy();
    expect(within(subnets).getByRole("button", { name: "rt-prod-default" })).toBeTruthy();
    expect(await screen.findByText("default-via-fw")).toBeTruthy();
    expect(screen.getByText("0.0.0.0/0")).toBeTruthy();
    expect(screen.getByText(/VirtualAppliance 10\.20\.254\.4/)).toBeTruthy();
    expect(screen.getByText(/1 with no route table/)).toBeTruthy();
    const peerings = screen.getAllByRole("heading", { name: "Peerings" })[0]!.closest("section")!;
    expect(within(peerings).getByText("Disconnected")).toBeTruthy();
    expect(within(peerings).getByText(/not readable by this account · 10\.60\.0\.0\/16/)).toBeTruthy();
  });

  it("shows an app with its plan, its subnet and what is open", async () => {
    const app = res("a1", "app-portal-prod", {
      props: { host_names: ["app-portal-prod.azurewebsites.net"], https_only: true, public_network_access: "Enabled", outbound_ips: ["20.74.0.31"], runtime: "Node 20 LTS" },
    });
    m.one.mockImplementation(async (id) => {
      if (id === "a1") return page(app, { exposure: ["public_access"], links_out: [link("plan", "p1", "asp-portal-prod"), link("vnet_integration", "n1", "snet-apps")] });
      if (id === "p1") return page(res("p1", "asp-portal-prod", { sku: { name: "P1v3" }, props: { workers: 2 } }));
      if (id === "n1") return page(res("n1", "snet-apps", { parent: { id: "v1", name: "vnet-prod" } }), { links_out: [link("route_table", "rt1", "rt-prod-default"), link("nsg", "g9", "nsg-apps")] });
      return page(res("rt1", "rt-prod-default", { props: { routes: [{}, {}] } }));
    });
    renderCloud("/inventory?scope=cloud&res=a1");
    expect(await screen.findByRole("heading", { name: "app-portal-prod" })).toBeTruthy();
    expect(await screen.findByText(/P1v3/)).toBeTruthy();
    expect(screen.getByText(/2 instances/)).toBeTruthy();
    expect(screen.getAllByText("Node 20 LTS").length).toBeGreaterThan(0);
    expect(await screen.findByText(/2 routes/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "nsg-apps" })).toBeTruthy();
    expect(screen.getAllByText("open to the internet").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Open in Azure portal" }).getAttribute("href")).toBe("https://portal.azure.com/x");
  });

  it("shows a machine with its network card, subnet and public address, and what changed", async () => {
    const vm = res("m1", "vm-pay-db-02", { type: "microsoft.compute/virtualmachines", label: "Virtual machine", category: "compute", state: "stopped", state_detail: "PowerState/deallocated", props: { size: "Standard_D2s_v5", os_type: "Linux" } });
    m.one.mockImplementation(async (id) => {
      if (id === "m1")
        return page(vm, {
          links_out: [link("nic", "c1", "nic-vm-1")],
          changes: [{ id: "x", run_id: "run1", at: PULL, change: "changed", fields: { "props.https_only": { old: false, new: true }, "relations.route_table": { old: ["/x/rt-old"], new: [] } } }],
        });
      if (id === "c1") return page(res("c1", "nic-vm-1", { props: { ip_configs: [{ name: "ipc", private_ip: "10.20.9.4" }] } }), { links_out: [link("subnet", "n2", "snet-mgmt"), link("public_ip", "i1", "pip-vm-1")] });
      return page(res("i1", "pip-vm-1", { props: { ip: "20.74.0.18" } }));
    });
    renderCloud("/inventory?scope=cloud&res=m1");
    expect(await screen.findByText("10.20.9.4")).toBeTruthy();
    expect(screen.getByRole("button", { name: "snet-mgmt" })).toBeTruthy();
    expect(await screen.findByText("20.74.0.18")).toBeTruthy();
    expect(screen.getByText("HTTPS only: No became Yes")).toBeTruthy();
    expect(screen.getByText("Route table: rt-old became none")).toBeTruthy();
  });
});
