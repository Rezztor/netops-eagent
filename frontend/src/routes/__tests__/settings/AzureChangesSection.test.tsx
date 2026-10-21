// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/api/azureChange";
import { ConfirmProvider, ToastProvider } from "@/components";
import { patternProblem } from "@/lib/referencePattern";
import { AzureChangesSection, formProblem } from "../../settings/sections/AzureChangesSection";
import type { SettingsContext } from "../../settings/types";
import { SECTIONS } from "../../settings/registry";

vi.mock("@/api/azureChange", async (importOriginal) => ({
  ...(await importOriginal<typeof api>()),
  getGuardrails: vi.fn(),
  saveGuardrails: vi.fn(),
  getKeys: vi.fn(),
  makeKey: vi.fn(),
  testRole: vi.fn(),
}));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} } }),
}));

const m = {
  get: vi.mocked(api.getGuardrails),
  save: vi.mocked(api.saveGuardrails),
  keys: vi.mocked(api.getKeys),
  make: vi.mocked(api.makeKey),
  role: vi.mocked(api.testRole),
};

const rails: api.Guardrails = {
  subscriptions: ["sub-1"],
  resource_groups: { existing: ["rg-web"], new_prefix: "rg-" },
  regions: [],
  resource_types: [],
  vm_sizes_max_vcpu: 8,
  usd_month_per_change: 250,
  usd_month_total: 1000,
  low_risk_usd_month: 20,
  max_resources: 40,
  allow_free_code: false,
  allow_unknown_price: false,
  reference_pattern: "(CR|SR)-?[0-9]{4,10}",
  document_valid_days: 14,
};
const keys: api.KeysState = {
  planner: { set: true, thumbprint: "AB:CD", expires_on: "2027-10-02", client_id: "c-1" },
  applier: { set: false },
  passphrase_set: true,
};

const ctx = { azure: { config: { tenant_id: "t-1", client_id: "c-1" } }, reload: vi.fn() } as unknown as SettingsContext;
const show = () =>
  render(
    <ToastProvider>
      <ConfirmProvider>
        <AzureChangesSection ctx={ctx} />
      </ConfirmProvider>
    </ToastProvider>,
  );

beforeEach(() => {
  m.get.mockResolvedValue(rails);
  m.keys.mockResolvedValue(keys);
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the reference pattern", () => {
  it("is refused when empty, too long, unreadable or matching nothing-at-all", () => {
    expect(patternProblem("(CR|SR)-?[0-9]{4,10}")).toBeNull();
    for (const bad of ["", "x".repeat(101), "(unclosed", "[0-9]*", "(CR)?"]) expect(patternProblem(bad)).not.toBeNull();
  });

  it("has a test box that says whether a sample matches, and saves nothing", async () => {
    show();
    const box = await screen.findByLabelText("Sample number");
    fireEvent.change(box, { target: { value: "cr-123456" } });
    expect(screen.getByTestId("reference-sample").textContent).toBe("matches");
    fireEvent.change(box, { target: { value: "CR-12" } });
    expect(screen.getByTestId("reference-sample").textContent).toBe("does not match");
    expect(m.save).not.toHaveBeenCalled();
  });

  it("blocks a save with a bad pattern before any request", async () => {
    show();
    fireEvent.change(await screen.findByLabelText("Number format"), { target: { value: "[0-9]*" } });
    expect(screen.getByRole("alert").textContent).toMatch(/empty number/);
    fireEvent.click(await screen.findByText("Save changes"));
    expect((await screen.findAllByText(/empty number/)).length).toBe(2); // beside the field and in the save bar
    expect(m.save).not.toHaveBeenCalled();
  });
});

describe("the guard rails", () => {
  it("sends lists and numbers in the shape the executor stores, with no delete setting anywhere", async () => {
    m.save.mockResolvedValue(rails);
    show();
    fireEvent.change(await screen.findByLabelText("Allowed regions"), { target: { value: "uaenorth, westeurope" } });
    fireEvent.change(screen.getByLabelText("Days a document may wait"), { target: { value: "7" } });
    fireEvent.click(await screen.findByText("Save changes"));
    await waitFor(() => expect(m.save).toHaveBeenCalled());
    expect(m.save.mock.calls[0]![0]).toEqual({
      ...rails,
      regions: ["uaenorth", "westeurope"],
      document_valid_days: 7,
    });
    expect(Object.keys(m.save.mock.calls[0]![0]).join()).not.toMatch(/delete|stop|restart|resize/);
    expect(screen.queryByText(/allow delet/i)).toBeNull();
  });

  it("refuses document days outside 1 to 30 and a negative ceiling", () => {
    const form = {
      subscriptions: "", existing: "", new_prefix: "rg-", regions: "", resource_types: "", vm_sizes_max_vcpu: "8",
      usd_month_per_change: "250", usd_month_total: "1000", low_risk_usd_month: "20", max_resources: "40",
      allow_unknown_price: false, allow_free_code: false, reference_pattern: "CR-[0-9]{4}", document_valid_days: "14",
    };
    expect(formProblem(form)).toBeNull();
    expect(formProblem({ ...form, document_valid_days: "31" })).toMatch(/1 to 30/);
    expect(formProblem({ ...form, document_valid_days: "0" })).toMatch(/1 to 30/);
    expect(formProblem({ ...form, usd_month_per_change: "-1" })).toMatch(/ceiling/);
  });

  it("explains itself when the plugin or the executor does not answer", async () => {
    m.get.mockRejectedValue(new Error("The cloud executor did not answer."));
    show();
    await screen.findByText("Azure changes are not available");
    expect(screen.getByText(/did not answer/)).toBeTruthy();
  });
});

describe("keys and the role test", () => {
  it("shows what is set without any key material", async () => {
    show();
    await screen.findByText("AB:CD");
    expect(screen.getByTestId("key-planner").textContent).toContain("AB:CD");
    expect(screen.getByTestId("key-applier").textContent).toContain("No key yet.");
  });

  it("makes a key and downloads only the public file", async () => {
    m.make.mockResolvedValue({ ok: true, public_certificate: "-----BEGIN CERTIFICATE-----", thumbprint: "EF", expires_on: "2027-10-03" });
    show();
    const applier = await screen.findByTestId("key-applier");
    fireEvent.change(applier.querySelector("input")!, { target: { value: "c-2" } });
    fireEvent.click(applier.querySelector("button")!);
    await waitFor(() => expect(m.make).toHaveBeenCalledWith("applier", "t-1", "c-2"));
    await waitFor(() => expect(m.keys).toHaveBeenCalledTimes(2));
  });

  it("says changes are off, with the reason, when the role is not right", async () => {
    m.role.mockResolvedValue({ ok: true, role_ok: false, reason: "The change app also holds Contributor." });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Test the role" }));
    const result = await screen.findByTestId("role-result");
    expect(result.getAttribute("data-tone")).toBe("crit");
    expect(result.textContent).toContain("changes are off");
    expect(result.textContent).toContain("Contributor");
  });

  it("shows verified when the role holds", async () => {
    m.role.mockResolvedValue({ ok: true, role_ok: true });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Test the role" }));
    expect((await screen.findByTestId("role-result")).textContent).toBe("role verified");
  });
});

describe("the registry entry", () => {
  it("is in Administration and needs plugins.manage", () => {
    const entry = SECTIONS.find((s) => s.id === "azure-changes")!;
    expect(entry.group).toBe("Administration");
    expect(entry.permission).toBe("plugins.manage");
  });
});
