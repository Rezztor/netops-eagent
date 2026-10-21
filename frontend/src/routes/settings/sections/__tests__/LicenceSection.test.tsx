// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LicenceStatus } from "@/api/licence";
import { ConfirmProvider } from "@/components";
import { LicenceSection } from "../LicenceSection";

const api = vi.hoisted(() => ({
  getLicenceStatus: vi.fn(),
  activateLicence: vi.fn(),
  checkInLicence: vi.fn(),
  releaseInstallation: vi.fn(),
  installLicenceFile: vi.fn(),
  requestActivationCode: vi.fn(),
}));
vi.mock("@/api/licence", () => api);

const reload = vi.fn();
vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h" } }),
  useReloadSession: () => reload,
}));

const DAY = 86400;
const T0 = 1790000000; // 21 Sep 2026

const LICENSED: LicenceStatus = {
  state: "licensed",
  reason: "ok",
  message: null,
  problem: "",
  clock_behind: false,
  agent_runs_allowed: true,
  restricted_at: T0 + 60 * DAY,
  days_left: 60,
  licence: {
    lic: "REZ-8F2A-0001",
    ver: 2,
    customer: "Example Bank",
    kind: "subscription",
    edition: "standard",
    features: ["azure", "solarwinds"],
    seats: 50,
    instances: 1,
    offline: false,
    contract_end: T0 + 365 * DAY,
    grace_days: 30,
  },
  lease_end: T0 + 30 * DAY,
  notice_until: null,
  seats_used: 37,
  devices_used: 12,
  devices_allowed: null,
  devices_warning: null,
  can_release: true,
  pending: null,
  last_attempt_at: T0,
  last_success_at: T0,
  last_result: "ok",
  next_attempt_at: T0 + DAY,
  installation_id: "a3f1c2d4e5f60718293a4b5c6d7e8f90",
  server_url: "https://license.rezztor.com",
};

const NO_LICENCE: LicenceStatus = {
  ...LICENSED,
  state: "restricted",
  reason: "no_licence",
  message: "No licence is installed.",
  agent_runs_allowed: false,
  restricted_at: null,
  days_left: null,
  licence: null,
  lease_end: null,
  can_release: false,
  last_attempt_at: null,
  last_success_at: null,
  last_result: null,
  next_attempt_at: null,
};

async function open(status: LicenceStatus) {
  api.getLicenceStatus.mockResolvedValue(status);
  render(
    <ConfirmProvider>
      <LicenceSection />
    </ConfirmProvider>,
  );
  await screen.findByText("This installation");
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the licence page", () => {
  it("shows a community build as free, with no activation form", async () => {
    api.getLicenceStatus.mockResolvedValue({
      ...NO_LICENCE,
      state: "community",
      reason: "community",
      message: "Community build: free, no limits.",
      agent_runs_allowed: true,
    });
    render(
      <ConfirmProvider>
        <LicenceSection />
      </ConfirmProvider>,
    );
    expect(await screen.findByText("Community build: free, no limits.")).toBeTruthy();
    const link = screen.getByRole("link", { name: "rezztor.com" });
    expect(link.getAttribute("href")).toBe("https://rezztor.com");
    expect(screen.getByText(/Rezztor Enterprise adds signed images/)).toBeTruthy();
    expect(screen.queryByLabelText("Activation key")).toBeNull();
    expect(screen.queryByText("This installation")).toBeNull();
  });

  it("shows what the administrator needs to see", async () => {
    await open(LICENSED);
    expect(screen.getByText("Licensed")).toBeTruthy();
    expect(screen.getByText("Example Bank")).toBeTruthy();
    expect(screen.getByText("standard")).toBeTruthy();
    expect(screen.getByText("37 used of 50")).toBeTruthy();
    expect(screen.getByText("12, no limit")).toBeTruthy();
    expect(screen.queryByText(/network devices/)).toBeNull();
    expect(screen.getByText("azure")).toBeTruthy();
    expect(screen.getByText("solarwinds")).toBeTruthy();
    // Contract end, lease end and the last check-in, each as a date a person reads.
    // (Asserted on the machine value: how a date is written depends on the reader's locale.)
    const shown = Array.from(document.querySelectorAll("time")).map((node) => node.getAttribute("datetime"));
    expect(shown).toEqual(["2027-09-21T14:13:20.000Z", "2026-10-21T14:13:20.000Z", "2026-09-21T14:13:20.000Z"]);
    expect(screen.getByText("ok")).toBeTruthy();
    expect(screen.getByText("a3f1c2d4e5f60718293a4b5c6d7e8f90")).toBeTruthy();
  });

  it("shows the device count against the limit, softly near it and as a warning over it", async () => {
    await open({ ...LICENSED, devices_used: 80, devices_allowed: 100 });
    expect(screen.getByText("80 of 100")).toBeTruthy();
    expect(screen.queryByText(/network devices/)).toBeNull();
    cleanup();

    await open({ ...LICENSED, devices_used: 92, devices_allowed: 100, devices_warning: "near" });
    expect(screen.getByText("92 of 100")).toBeTruthy();
    expect(screen.getByText(/has 92 of the 100 network devices the\s+licence allows/)).toBeTruthy();
    cleanup();

    await open({ ...LICENSED, devices_used: 130, devices_allowed: 100, devices_warning: "over" });
    expect(screen.getByText("130 of 100")).toBeTruthy();
    const warning = screen.getByText(/has 130 network devices and the licence allows\s+100/);
    expect(warning.textContent).toMatch(/Everything keeps working/);
  });

  it("warns in plain words during grace, and says why a check-in failed", async () => {
    await open({
      ...LICENSED,
      state: "grace2",
      reason: "ended",
      days_left: 9,
      pending: "checkin",
      last_result: "The licence server could not be reached.",
    });
    expect(screen.getByText("Grace, final period")).toBeTruthy();
    expect(screen.getByText(/Everything works for 9 days more/)).toBeTruthy();
    expect(screen.getByText(/The licence server could not be reached\./)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again now" })).toBeTruthy();
  });

  it("says what a restricted installation still does, and that the clock is behind", async () => {
    await open({ ...NO_LICENCE, clock_behind: true });
    expect(screen.getByText("Restricted")).toBeTruthy();
    expect(screen.getByText("No licence is installed.")).toBeTruthy();
    expect(screen.getByText(/Sign-in, reading,\s+export and settings keep working/)).toBeTruthy();
    expect(screen.getByText(/clock reads earlier/)).toBeTruthy();
    // Nothing to release and nothing to check in with.
    expect(screen.queryByRole("button", { name: "Release this installation" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Check in now" })).toBeNull();
  });

  it("activates with the key, shows a refusal, and does not keep the key on screen after success", async () => {
    await open(NO_LICENCE);
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    expect(screen.getByText("Enter the activation key you were given.")).toBeTruthy();
    expect(api.activateLicence).not.toHaveBeenCalled();

    const field = screen.getByLabelText("Activation key") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "REZ-AAAAA-AAAAA-AAAAA-AAAAA-AAAAB" } });
    api.activateLicence.mockResolvedValueOnce({
      ok: false,
      error: "That activation key has a typing mistake: its check character does not match.",
      licence: NO_LICENCE,
    });
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    await screen.findByText(/typing mistake/);
    expect(field.value).toBe("REZ-AAAAA-AAAAA-AAAAA-AAAAA-AAAAB");
    expect(reload).not.toHaveBeenCalled();

    api.activateLicence.mockResolvedValueOnce({ ok: true, licence: LICENSED });
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    await screen.findByText("The licence is active.");
    expect(api.activateLicence).toHaveBeenLastCalledWith("REZ-AAAAA-AAAAA-AAAAA-AAAAA-AAAAB");
    expect(field.value).toBe("");
    expect(screen.getByText("Example Bank")).toBeTruthy();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("releases the installation only after a confirmation", async () => {
    await open(LICENSED);
    fireEvent.click(screen.getByRole("button", { name: "Release this installation" }));
    await screen.findByText("Release this installation?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByText("Release this installation?")).toBeNull());
    expect(api.releaseInstallation).not.toHaveBeenCalled();

    api.releaseInstallation.mockResolvedValueOnce({
      ok: true,
      licence: { ...LICENSED, state: "restricted", reason: "no_lease", can_release: false, lease_end: null },
    });
    fireEvent.click(screen.getByRole("button", { name: "Release this installation" }));
    await screen.findByText("Release this installation?");
    fireEvent.click(screen.getByRole("button", { name: "Release" }));
    await screen.findByText("The installation was released.");
    expect(api.releaseInstallation).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Restricted")).toBeTruthy();
  });

  it("checks in on request", async () => {
    await open(LICENSED);
    api.checkInLicence.mockResolvedValueOnce({ ok: true, licence: LICENSED });
    fireEvent.click(screen.getByRole("button", { name: "Check in now" }));
    await screen.findByText("Checked in with the licence server.");
  });

  it("offers the offline exchange: a request code to copy and a licence file to load", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    await open(NO_LICENCE);
    fireEvent.click(screen.getByRole("button", { name: "No internet access?" }));

    fireEvent.click(screen.getByRole("button", { name: "Make request code" }));
    expect(screen.getByText("Name this deployment before making a request code.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Deployment name"), { target: { value: "DR site" } });
    api.requestActivationCode.mockResolvedValueOnce({ ok: true, request_code: "REZ-REQ-04G0H-XYZ12", licence: NO_LICENCE });
    fireEvent.click(screen.getByRole("button", { name: "Make request code" }));
    await waitFor(() =>
      expect((screen.getByLabelText("Request code") as HTMLTextAreaElement).value).toBe("REZ-REQ-04G0H-XYZ12"),
    );
    expect(api.requestActivationCode).toHaveBeenCalledWith("DR site");
    fireEvent.click(screen.getByRole("button", { name: "Copy request code" }));
    expect(writeText).toHaveBeenCalledWith("REZ-REQ-04G0H-XYZ12");

    fireEvent.click(screen.getByRole("button", { name: "Load licence file" }));
    expect(screen.getByText("Choose the licence file, or paste its text.")).toBeTruthy();
    expect(api.installLicenceFile).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Licence file"), { target: { value: '{"v":2}' } });
    api.installLicenceFile.mockResolvedValueOnce({
      ok: false,
      error: "That licence file was issued for another installation.",
      licence: NO_LICENCE,
    });
    fireEvent.click(screen.getByRole("button", { name: "Load licence file" }));
    await screen.findByText("That licence file was issued for another installation.");

    api.installLicenceFile.mockResolvedValueOnce({
      ok: true,
      licence: { ...LICENSED, can_release: false, lease_end: null, licence: { ...LICENSED.licence!, offline: true } },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load licence file" }));
    await screen.findByText("The licence file was loaded.");
    expect(api.installLicenceFile).toHaveBeenLastCalledWith('{"v":2}');
    vi.unstubAllGlobals();
  });
});
