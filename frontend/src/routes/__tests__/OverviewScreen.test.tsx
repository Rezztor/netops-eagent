// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PERMISSIONS } from "@/api/types";

let permissions: string[] = [];
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    user: { display_name: "Ada Admin", role_title: permissions.length > 3 ? "Administrator" : "Standard User" },
    permissions,
    version: { version: "U v2026.10.17+14", commit_time: "" },
    runtime: { is_development: false },
    licence: { state: "licensed" },
  }),
  useCan: (p: string) => permissions.includes(p),
}));
vi.mock("@/lib/realtime", () => ({
  useRealtime: () => ({ state: { contexts: [{ id: "c1", name: "VLAN 20 on sw-01", last_message: "2026-10-19T09:00:00Z" }] } }),
}));
vi.mock("./../AppShell", () => ({ displayVersion: (v: string) => v.replace(/^[A-Z] /, "") }));
vi.mock("@/api/models", () => ({
  getModelConfig: vi.fn(async () => ({ model_configured: true, config: { chat_model: { provider: "openai" } }, api_key_status: { openai: true } })),
}));
vi.mock("@/api/networkAutomation", () => ({
  getNetworkConfig: vi.fn(async () => ({ config: { configured: false, executor_token_set: false } })),
  getApprovalsWaiting: vi.fn(async () => [{ id: "x", summary: "Set description on Ethernet0", playbook_name: "", requested_by_name: "Eve" }]),
}));
vi.mock("@/api/inventorySummary", () => ({ getInventorySummary: vi.fn(async () => ({ ok: true, devices: 0 })) }));
vi.mock("@/api/admin", () => ({
  getSettings: vi.fn(async () => { throw new Error("down"); }),
  listUsers: vi.fn(async () => ({ users: [{}, {}] })),
}));

import { OverviewScreen } from "../OverviewScreen";

afterEach(() => cleanup());

function show() {
  render(<MemoryRouter><OverviewScreen /></MemoryRouter>);
}

describe("home for an administrator", () => {
  it("lists what is left to set up, each step linking to where it is done", async () => {
    permissions = Object.values(PERMISSIONS);
    show();
    expect(await screen.findByText("3 of 6 done")).toBeTruthy();
    const executor = screen.getByRole("link", { name: /Connect network automation/ });
    expect(executor.getAttribute("href")).toBe("/settings/network-automation");
    expect(executor.parentElement!.textContent).toContain("(not done)");
    // Mail could not be checked: said so, not shown as done or not done.
    expect(screen.getByRole("link", { name: /Set up email/ }).parentElement!.textContent).toContain("(could not check)");
    expect(screen.getByRole("link", { name: "Set description on Ethernet0" }).getAttribute("href")).toBe("/approvals?change=x");
  });
});

describe("home for anyone", () => {
  it("speaks in roles and plain capabilities, never permission codes or API paths", async () => {
    permissions = [PERMISSIONS.chatUse];
    show();
    await waitFor(() => expect(screen.getByText("Chat with the agent")).toBeTruthy());
    expect(screen.queryByText("Get started")).toBeNull();
    expect(screen.getByRole("link", { name: "VLAN 20 on sw-01" }).getAttribute("href")).toBe("/chat?ctx=c1");
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("chat.use");
    expect(text).not.toContain("/api/bootstrap");
    expect(text).toContain("v2026.10.17+14");
    expect(text).not.toContain("U v2026");
  });
});
