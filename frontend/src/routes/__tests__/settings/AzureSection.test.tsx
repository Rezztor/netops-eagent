// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as azureApi from "@/api/azure";
import { ConfirmProvider, ToastProvider } from "@/components";
import { AzureSection, SIGN_IN_FAILURES } from "../../settings/sections/AzureSection";
import type { SettingsContext } from "../../settings/types";

vi.mock("@/api/azure", async (importOriginal) => ({
  ...(await importOriginal<typeof azureApi>()),
  getAzureStatus: vi.fn(),
  saveAzureConfig: vi.fn(),
  generateAzureCertificate: vi.fn(),
  generateAzureLogsCertificate: vi.fn(),
  getAzureLogsPublicCertificate: vi.fn(),
  getAzurePublicCertificate: vi.fn(),
  testAzureConnection: vi.fn(),
}));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} } }),
}));

const m = {
  status: vi.mocked(azureApi.getAzureStatus),
  save: vi.mocked(azureApi.saveAzureConfig),
  generate: vi.mocked(azureApi.generateAzureCertificate),
  publicCert: vi.mocked(azureApi.getAzurePublicCertificate),
  generateLogs: vi.mocked(azureApi.generateAzureLogsCertificate),
  logsPublicCert: vi.mocked(azureApi.getAzureLogsPublicCertificate),
  test: vi.mocked(azureApi.testAzureConnection),
};

const config = {
  cloud: "public", tenant_id: "t-1", client_id: "c-1", auth: "certificate", secret_expires_on: "",
  subscriptions_mode: "all", subscription_ids: [],
  schedule: { frequency: "weekly", weekday: "sat", time: "02:00", timezone: "UTC" },
  keep_removed_days: 90, keep_history_days: 365, min_pull_interval_minutes: 15,
  timeout_seconds: 30, page_size: 1000, max_resources: 100000,
} as azureApi.AzureConfig;
const noCredential: azureApi.AzureCredential = { kind: "", set: false, fingerprint: "", expires_on: "" };
const certificate: azureApi.AzureCredential = { kind: "certificate", set: true, fingerprint: "9F:2C:71:B4", expires_on: "2027-10-02" };

const noLogsCredential: azureApi.AzureLogsCredential = { set: false, fingerprint: "", expires_on: "" };
const logsCertificate: azureApi.AzureLogsCredential = { set: true, fingerprint: "AB:12:CD:34", expires_on: "2027-10-03" };
const WS = "5d1a7c30-6c1e-4b0e-8f4a-2b9d3e5f7a01";

const reload = vi.fn();
function renderSection(credential = noCredential, over: Partial<azureApi.AzureConfig> = {}, logsCredential = noLogsCredential) {
  const ctx = {
    azure: { ok: true, enabled: true, config: { ...config, ...over }, credential, logs_credential: logsCredential },
    reload,
  } as unknown as SettingsContext;
  render(
    <ToastProvider>
      <ConfirmProvider>
        <AzureSection ctx={ctx} />
      </ConfirmProvider>
    </ToastProvider>,
  );
}

const probe = () => screen.getByTestId("azure-probe");

beforeEach(() => {
  m.status.mockResolvedValue({ next_pull_at: "2026-10-03T02:00:00Z" } as azureApi.AzureStatus);
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the credential", () => {
  it("keeps the secret field blank on load, and an untouched field sends no secret", async () => {
    m.save.mockResolvedValue({ ok: true, enabled: true, config: { ...config, auth: "secret" }, credential: { ...noCredential, kind: "secret", set: true } });
    renderSection({ ...noCredential, kind: "secret", set: true }, { auth: "secret" });
    const field = screen.getByPlaceholderText("••••••••") as HTMLInputElement;
    expect(field.value).toBe("");
    expect(screen.getByText(/Already set/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Time"), { target: { value: "03:30" } });
    fireEvent.click(await screen.findByText("Save changes"));
    await waitFor(() => expect(m.save).toHaveBeenCalled());
    expect(m.save.mock.calls[0]![0]).not.toHaveProperty("secret");
    // After a save the field is blank again.
    expect(field.value).toBe("");
  });

  it("sends a typed secret once and blanks the field", async () => {
    m.save.mockResolvedValue({ ok: true, enabled: true, config: { ...config, auth: "secret" }, credential: { ...noCredential, kind: "secret", set: true } });
    renderSection(noCredential, { auth: "secret" });
    const field = screen.getByLabelText("Client secret");
    fireEvent.change(field, { target: { value: "s3cret-value" } });
    fireEvent.click(await screen.findByText("Save changes"));
    await waitFor(() => expect(m.save).toHaveBeenCalled());
    expect(m.save.mock.calls[0]![0]).toMatchObject({ secret: "s3cret-value" });
    await waitFor(() => expect((field as HTMLInputElement).value).toBe(""));
  });

  it("asks before replacing an existing certificate", async () => {
    renderSection(certificate);
    fireEvent.click(screen.getByText("Create new certificate"));
    expect(await screen.findByText("Create a new certificate?")).toBeTruthy();
    fireEvent.click(screen.getByText("Cancel"));
    await waitFor(() => expect(screen.queryByText("Create a new certificate?")).toBeNull());
    expect(m.generate).not.toHaveBeenCalled();
  });

  it("creates a certificate, shows its fingerprint and date, and downloads only the public file", async () => {
    m.generate.mockResolvedValue({ ok: true, credential: certificate, public_certificate: "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n" });
    m.publicCert.mockResolvedValue({ ok: true, public_certificate: "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n" });
    renderSection();
    expect(screen.getByText("No certificate yet.")).toBeTruthy();

    fireEvent.click(screen.getByText("Create certificate"));
    expect(await screen.findByText("9F:2C:71:B4")).toBeTruthy();
    expect(screen.getByTestId("azure-certificate").textContent).toMatch(/2027/);

    fireEvent.click(screen.getByText("Download public file (.cer)"));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0]![0] as Blob;
    expect(await blob.text()).not.toMatch(/PRIVATE KEY/);
    expect(await blob.text()).toMatch(/BEGIN CERTIFICATE/);
    expect(document.body.textContent).not.toMatch(/PRIVATE KEY/);
  });
});

describe("Test connection", () => {
  it.each(Object.keys(SIGN_IN_FAILURES))("renders the %s failure as its plain sentence", async (reason) => {
    m.test.mockResolvedValue({ ok: true, signed_in: false, reason, message: "AADSTS70001 raw", subscriptions: [], resources: 0 });
    renderSection(certificate);
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(probe().textContent).toBe(SIGN_IN_FAILURES[reason]));
    expect(probe().textContent).not.toMatch(/AADSTS/);
  });

  it("says so when it signs in but no subscription is readable", async () => {
    m.test.mockResolvedValue({ ok: true, signed_in: true, message: "", subscriptions: [], resources: 0 });
    renderSection(certificate);
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(probe().textContent).toMatch(/Reader role/));
  });

  it("fills the subscription list from a successful test", async () => {
    m.test.mockResolvedValue({
      ok: true, signed_in: true, message: "", resources: 1284,
      subscriptions: [{ id: "s1", name: "Production", state: "Enabled" }, { id: "s2", name: "Sandbox", state: "Enabled" }],
    });
    renderSection(certificate, { subscriptions_mode: "only" });
    expect(screen.getByText(/Press Test connection to list/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(probe().textContent).toMatch(/Signed in. Production, Sandbox. 1,284 resources visible/));
    const prod = screen.getByLabelText("Production") as HTMLInputElement;
    expect(prod.checked).toBe(false);
    fireEvent.click(prod);
    expect(prod.checked).toBe(true);
    expect(screen.getByText("1 unsaved change.")).toBeTruthy();
  });

  it("asks to save first while there are unsaved edits", () => {
    renderSection(certificate);
    fireEvent.change(screen.getByLabelText("Time"), { target: { value: "04:00" } });
    expect((screen.getByRole("button", { name: "Test connection" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("the schedule", () => {
  it("shows the day only for weekly, nothing for off, and the next pull", async () => {
    renderSection(certificate);
    expect(screen.getByLabelText("Day")).toBeTruthy();
    expect(await screen.findByTestId("azure-next-pull")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("azure-next-pull").textContent).not.toBe("—"));

    fireEvent.change(screen.getByLabelText("Pull the inventory"), { target: { value: "daily" } });
    expect(screen.queryByLabelText("Day")).toBeNull();
    expect(screen.getByLabelText("Time")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Pull the inventory"), { target: { value: "off" } });
    expect(screen.queryByLabelText("Time")).toBeNull();
    expect(screen.getByTestId("azure-next-pull").textContent).toBe("Off");
  });

  it("saves the schedule fields", async () => {
    m.save.mockResolvedValue({ ok: true, enabled: true, config, credential: certificate });
    renderSection(certificate);
    fireEvent.change(screen.getByLabelText("Day"), { target: { value: "sun" } });
    fireEvent.change(screen.getByLabelText("Time"), { target: { value: "05:15" } });
    fireEvent.change(screen.getByLabelText("Time zone"), { target: { value: "Asia/Dubai" } });
    fireEvent.click(await screen.findByText("Save changes"));
    await waitFor(() => expect(m.save).toHaveBeenCalled());
    expect(m.save.mock.calls[0]![0].config?.schedule).toEqual({
      frequency: "weekly", weekday: "sun", time: "05:15", timezone: "Asia/Dubai",
    });
  });
});

describe("live reads and log queries", () => {
  it("shows the live-read approval as every time when the setting is absent, and saves a change", async () => {
    m.save.mockResolvedValue({ ok: true, enabled: true, config, credential: certificate });
    renderSection(certificate);
    const select = screen.getByLabelText("Live read approval") as HTMLSelectElement;
    expect(select.value).toBe("every_time");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["every_time", "once_per_chat", "log_only"]);
    fireEvent.change(select, { target: { value: "once_per_chat" } });
    fireEvent.click(await screen.findByText("Save changes"));
    await waitFor(() => expect(m.save).toHaveBeenCalled());
    expect(m.save.mock.calls[0]![0].config?.read_approval).toBe("once_per_chat");
  });

  it("loads the saved log settings into the fields, one workspace per line", () => {
    renderSection(certificate, {
      read_approval: "log_only",
      logs: { client_id: "c-log", workspaces: [{ name: "prod", id: WS }], tables: ["Heartbeat", "Perf"], max_timespan: "P1D" },
    }, logsCertificate);
    expect((screen.getByLabelText("Live read approval") as HTMLSelectElement).value).toBe("log_only");
    expect((screen.getByLabelText("Log application ID") as HTMLInputElement).value).toBe("c-log");
    expect((screen.getByLabelText("Log workspaces") as HTMLTextAreaElement).value).toBe(`prod ${WS}`);
    expect((screen.getByLabelText("Log tables") as HTMLInputElement).value).toBe("Heartbeat, Perf");
    expect((screen.getByLabelText("Longest log time range") as HTMLInputElement).value).toBe("P1D");
    expect(screen.getByText("log app set")).toBeTruthy();
  });

  it("saves the log settings as the server takes them: workspaces and tables as lists", async () => {
    m.save.mockResolvedValue({ ok: true, enabled: true, config, credential: certificate });
    renderSection(certificate);
    fireEvent.change(screen.getByLabelText("Log application ID"), { target: { value: " c-log " } });
    fireEvent.change(screen.getByLabelText("Log workspaces"), { target: { value: `prod ${WS}\n\ndev=${WS.replace("5d", "6e")}\n` } });
    fireEvent.change(screen.getByLabelText("Log tables"), { target: { value: "Heartbeat, AZFWNetworkRule  Perf" } });
    fireEvent.change(screen.getByLabelText("Longest log time range"), { target: { value: "P3D" } });
    fireEvent.click(await screen.findByText("Save changes"));
    await waitFor(() => expect(m.save).toHaveBeenCalled());
    expect(m.save.mock.calls[0]![0].config?.logs).toEqual({
      client_id: "c-log",
      workspaces: [{ name: "prod", id: WS }, { name: "dev", id: WS.replace("5d", "6e") }],
      tables: ["Heartbeat", "AZFWNetworkRule", "Perf"],
      max_timespan: "P3D",
    });
  });

  it("shows the server's refusal of a wrong log setting and keeps the edit", async () => {
    m.save.mockRejectedValue(new Error("The log application (client) ID must be a GUID."));
    renderSection(certificate);
    fireEvent.change(screen.getByLabelText("Log application ID"), { target: { value: "nope" } });
    fireEvent.click(await screen.findByText("Save changes"));
    expect(await screen.findByText("The log application (client) ID must be a GUID.")).toBeTruthy();
    expect((screen.getByLabelText("Log application ID") as HTMLInputElement).value).toBe("nope");
  });

  it("creates the log certificate, shows its thumbprint and downloads only the public file", async () => {
    const pem = "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n";
    m.generateLogs.mockResolvedValue({ ok: true, logs_credential: logsCertificate, public_certificate: pem });
    m.logsPublicCert.mockResolvedValue({ ok: true, public_certificate: pem });
    renderSection(certificate);
    expect(screen.getByText("No log certificate yet.")).toBeTruthy();
    fireEvent.click(screen.getByText("Create log certificate"));
    expect(await screen.findByText("AB:12:CD:34")).toBeTruthy();
    expect(screen.getByTestId("azure-logs-certificate").textContent).toMatch(/2027/);
    fireEvent.click(screen.getByText("Download log public file (.cer)"));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    const text = await (vi.mocked(URL.createObjectURL).mock.calls[0]![0] as Blob).text();
    expect(text).toMatch(/BEGIN CERTIFICATE/);
    expect(text).not.toMatch(/PRIVATE KEY/);
    expect(document.body.textContent).not.toMatch(/PRIVATE KEY/);
  });

  it("asks before replacing the log certificate and never touches the read certificate", async () => {
    renderSection(certificate, {}, logsCertificate);
    fireEvent.click(screen.getByText("Create new log certificate"));
    expect(await screen.findByText("Create a new log certificate?")).toBeTruthy();
    fireEvent.click(screen.getByText("Cancel"));
    await waitFor(() => expect(screen.queryByText("Create a new log certificate?")).toBeNull());
    expect(m.generateLogs).not.toHaveBeenCalled();
    expect(m.generate).not.toHaveBeenCalled();
  });
});
