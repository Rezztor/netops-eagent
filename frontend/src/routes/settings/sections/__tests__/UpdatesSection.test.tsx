// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ListedUpdate, UpdateAvailable, UpdateStatus, UpdatesList } from "@/api/updates";
import { ConfirmProvider } from "@/components";
import { UpdatesSection } from "../UpdatesSection";

const api = vi.hoisted(() => ({
  fetchUpdateStatus: vi.fn(),
  uploadUpdate: vi.fn(),
  requestUpdate: vi.fn(),
  downloadUpdate: vi.fn(),
  setDailyCheck: vi.fn(),
}));
vi.mock("@/api/updates", async () => {
  const actual = await vi.importActual("@/api/updates");
  return { ...(actual as object), ...api };
});

const reload = vi.fn();
vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h" } }),
  useReloadSession: () => reload,
}));

const IDLE: UpdateAvailable = {
  available: true,
  phase: "idle",
  installed: { version: "2026.10.08", base: "2026.10.08", updated_at: null },
  update_key: "age1q7mtestk3xw9d",
  keys: ["release-2026a"],
  package: null,
  steps: [],
  error: null,
  result: null,
  history: [],
  request: null,
  at: "2026-10-12T14:06:02Z",
};

const PACKAGE = {
  file: "rezztor-eagent-update-2026.10.12.rezupd",
  size: 10_900_000,
  version: "2026.10.12",
  base: "2026.10.08",
  restart: "application" as const,
  database_change: false,
  notes: ["Models: the context length of Ollama Cloud models is now found."],
  key: "release-2026a",
  created: "2026-10-12T09:30:00Z",
};

async function open(status: UpdateStatus) {
  api.fetchUpdateStatus.mockResolvedValue(status);
  render(
    <ConfirmProvider>
      <UpdatesSection />
    </ConfirmProvider>,
  );
  await waitFor(() => expect(api.fetchUpdateStatus).toHaveBeenCalled());
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("the updates page", () => {
  it("offers nothing when there is no update service", async () => {
    await open({ available: false });
    expect(screen.getByText(/has no update service/)).toBeTruthy();
    expect(screen.getByText(/upgraded by rolling a new image/)).toBeTruthy();
    expect(screen.queryByText("Install an update from a file")).toBeNull();
    expect(screen.queryByText("Available updates")).toBeNull();
    expect(screen.queryByRole("button", { name: /Choose file/ })).toBeNull();
  });

  it("shows this installation and the drop zone when idle", async () => {
    await open(IDLE);
    expect(screen.getByText("This installation")).toBeTruthy();
    expect(screen.getByText("2026.10.08")).toBeTruthy();
    expect(screen.getByText("none since installation")).toBeTruthy();
    expect(screen.getByText("age1q7mtestk3xw9d")).toBeTruthy();
    expect(screen.getByText("Drop the update file here")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Choose file…" })).toBeTruthy();
    expect(screen.getByText(/Apply an update/)).toBeTruthy();
  });

  it("shows a refused upload as a critical note", async () => {
    await open({
      ...IDLE,
      error: {
        code: "bad_signature",
        text: "This file was not accepted: its signature does not match. Nothing was installed.",
      },
    });
    expect(screen.getByText(/signature does not match/)).toBeTruthy();
  });

  it("uploads a file, shows a refusal from the API, and keeps the drop zone", async () => {
    await open(IDLE);
    api.uploadUpdate.mockResolvedValueOnce({
      ok: false,
      error: "This installation cannot open the file. Send Rezztor the update key shown on this page.",
    });
    const input = screen.getByLabelText("Choose update file") as HTMLInputElement;
    const file = new File(["pkg"], "patch.rezupd", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [file] } });
    await screen.findByText(/cannot open the file/);
    expect(api.uploadUpdate).toHaveBeenCalledWith(file, expect.any(Function));
    expect(screen.getByRole("button", { name: "Choose file…" })).toBeTruthy();
  });

  it("shows the package card when checked", async () => {
    await open({ ...IDLE, phase: "checked", package: PACKAGE });
    expect(screen.getByText("Update 2026.10.12")).toBeTruthy();
    expect(screen.getByText("signed by Rezztor")).toBeTruthy();
    expect(screen.getByText("fits this version")).toBeTruthy();
    expect(screen.getByText("restart needed")).toBeTruthy();
    expect(screen.getByText("2026.10.08 → 2026.10.12")).toBeTruthy();
    expect(screen.getByText(/context length of Ollama Cloud/)).toBeTruthy();
    expect(screen.getByText(/The application, about a minute/)).toBeTruthy();
    expect(screen.getByText(/no change/)).toBeTruthy();
    expect(screen.getByText("release-2026a")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Discard" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Install" })).toBeTruthy();
  });

  it("lists installing steps and hides the action buttons", async () => {
    await open({
      ...IDLE,
      phase: "installing",
      package: PACKAGE,
      steps: [
        { id: "signature", text: "Signature checked on the host", state: "done" },
        { id: "open", text: "Opened, and every file compared with the signed list", state: "done" },
        { id: "space", text: "Enough free space: 41 GB free, 0.3 GB needed", state: "done" },
        { id: "build", text: "Building the new application image", state: "running" },
        { id: "ready", text: "Ready to restart", state: "waiting" },
      ],
    });
    expect(screen.getByText("Installing 2026.10.12")).toBeTruthy();
    expect(screen.getByText("Building the new application image")).toBeTruthy();
    expect(screen.getByText("step 4 of 5")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  });

  it("asks for confirmation before restart when staged", async () => {
    await open({ ...IDLE, phase: "staged", package: PACKAGE });
    expect(screen.getByText("Restart to finish")).toBeTruthy();
    expect(screen.getByText(/beside the running version/)).toBeTruthy();
    expect(screen.queryByText(/agent runs/)).toBeNull();
    expect(screen.queryByText(/signed in/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Restart now" }));
    await screen.findByText("Restart to finish the update?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByText("Restart to finish the update?")).toBeNull());
    expect(api.requestUpdate).not.toHaveBeenCalled();

    api.requestUpdate.mockResolvedValueOnce({ ok: true, request: "req-1" });
    api.fetchUpdateStatus.mockResolvedValue({ ...IDLE, phase: "restarting", package: PACKAGE });
    fireEvent.click(screen.getByRole("button", { name: "Restart now" }));
    await screen.findByText("Restart to finish the update?");
    const confirmButtons = screen.getAllByRole("button", { name: "Restart now" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);
    await screen.findByText("Rezztor EAgent is restarting");
    expect(api.requestUpdate).toHaveBeenCalledWith("restart");
  });

  it("survives a failed status call while restarting, then shows the result", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const done: UpdateAvailable = {
      ...IDLE,
      phase: "idle",
      installed: { version: "2026.10.12", base: "2026.10.08", updated_at: "2026-10-12T14:06:00Z" },
      result: {
        outcome: "installed",
        version: "2026.10.12",
        at: "2026-10-12T14:06:00Z",
        text: "Updated to 2026.10.12. The restart took 48 seconds.",
        seconds: 48,
      },
      history: [
        { at: "2026-10-12T14:06:00Z", version: "2026.10.12", by: "admin", outcome: "installed" },
      ],
    };
    api.fetchUpdateStatus
      .mockResolvedValueOnce({ ...IDLE, phase: "restarting", package: PACKAGE })
      .mockRejectedValueOnce(new Error("network"))
      .mockRejectedValueOnce(new Error("502"))
      .mockResolvedValue(done);

    render(
      <ConfirmProvider>
        <UpdatesSection />
      </ConfirmProvider>,
    );
    await screen.findByText("Rezztor EAgent is restarting");
    expect(screen.getByText(/Starting version 2026.10.12/)).toBeTruthy();

    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByText("Rezztor EAgent is restarting")).toBeTruthy();

    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByText("Rezztor EAgent is restarting")).toBeTruthy();

    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => expect(screen.getByText(/Updated to 2026.10.12/)).toBeTruthy());
    expect(reload).toHaveBeenCalled();
    expect(screen.getByText("History")).toBeTruthy();
  });

  it("stays on the restarting view until the host has taken the request, then follows it to the end", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const staged: UpdateAvailable = { ...IDLE, phase: "staged", package: PACKAGE };
    const done: UpdateAvailable = {
      ...IDLE,
      request: "req-1",
      installed: { version: "2026.10.12", base: "2026.10.08", updated_at: "2026-10-12T14:06:00Z" },
      result: { outcome: "installed", version: "2026.10.12", at: "2026-10-12T14:06:00Z", text: "Updated to 2026.10.12.", seconds: 48 },
    };
    await open(staged);
    api.requestUpdate.mockResolvedValueOnce({ ok: true, request: "req-1" });
    // The host needs a moment: the first answers still say "staged", with the request not yet taken.
    api.fetchUpdateStatus
      .mockResolvedValueOnce(staged)
      .mockResolvedValueOnce(staged)
      .mockResolvedValueOnce({ ...staged, phase: "restarting", request: "req-1" })
      .mockRejectedValueOnce(new Error("502"))
      .mockResolvedValue(done);
    fireEvent.click(screen.getByRole("button", { name: "Restart now" }));
    await screen.findByText("Restart to finish the update?");
    const buttons = screen.getAllByRole("button", { name: "Restart now" });
    fireEvent.click(buttons[buttons.length - 1]!);
    await screen.findByText("Rezztor EAgent is restarting");
    for (let tick = 0; tick < 4; tick += 1) {
      await vi.advanceTimersByTimeAsync(2000);
      expect(screen.getByText("Rezztor EAgent is restarting")).toBeTruthy();
    }
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => expect(screen.getByText("Updated to 2026.10.12.")).toBeTruthy());
    expect(reload).toHaveBeenCalled();
  });

  it("keeps asking after Remove this update until the host has removed it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const staged: UpdateAvailable = { ...IDLE, phase: "staged", package: PACKAGE };
    await open(staged);
    api.requestUpdate.mockResolvedValueOnce({ ok: true, request: "req-2" });
    api.fetchUpdateStatus.mockResolvedValueOnce(staged).mockResolvedValue({ ...IDLE, request: "req-2" });
    fireEvent.click(screen.getByRole("button", { name: "Remove this update" }));
    await waitFor(() => expect(api.requestUpdate).toHaveBeenCalledWith("discard"));
    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByText("Restart to finish")).toBeTruthy();
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => expect(screen.getByText("Install an update from a file")).toBeTruthy());
    // The banner that said "waits for a restart" comes from the session, so it is read again.
    expect(reload).toHaveBeenCalled();
  });

  it("reads the session again when the appliance went back to the old version", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.fetchUpdateStatus
      .mockResolvedValueOnce({ ...IDLE, phase: "restarting", package: PACKAGE })
      .mockResolvedValue({
        ...IDLE,
        result: { outcome: "rolled_back", version: "2026.10.12", at: "2026-10-12T14:06:00Z", text: "The appliance returned to 2026.10.08. Nothing was lost.", seconds: null },
      });
    render(
      <ConfirmProvider>
        <UpdatesSection />
      </ConfirmProvider>,
    );
    await screen.findByText("Rezztor EAgent is restarting");
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => expect(screen.getByText(/returned to 2026.10.08/)).toBeTruthy());
    expect(reload).toHaveBeenCalled();
  });

  it("shows why the host refused, on the page the administrator is looking at", async () => {
    await open({
      ...IDLE,
      phase: "checked",
      package: PACKAGE,
      error: { code: "not_for_us", text: "This installation cannot open the file. Send Rezztor the update key shown here: age1q7m" },
    });
    expect(screen.getByText(/cannot open the file/)).toBeTruthy();
  });

  it("shows a rolled-back result as a critical note", async () => {
    await open({
      ...IDLE,
      result: {
        outcome: "rolled_back",
        version: "2026.10.12",
        at: "2026-10-12T14:06:00Z",
        text: "Version 2026.10.12 did not answer within 3 minutes after the restart. The appliance returned to 2026.10.08. Nothing was lost.",
        seconds: null,
      },
    });
    expect(screen.getByText(/did not answer within 3 minutes/)).toBeTruthy();
  });
});

function listed(version: string, more: Partial<ListedUpdate> = {}): ListedUpdate {
  return {
    version,
    base: "2026.10.08",
    min_from: "2026.10.08",
    bridge: false,
    released_at: "2026-10-21T09:00:00Z",
    security: false,
    size: 383_436_800,
    sha256: "0".repeat(64),
    notes: [`Notes of ${version}.`],
    available_at: "2026-10-21T09:00:00Z",
    downloadable: false,
    ...more,
  };
}

function withList(more: Partial<UpdatesList> = {}, status: Partial<UpdateAvailable> = {}): UpdateAvailable {
  return {
    ...IDLE,
    daily_check: true,
    updates: {
      checked_at: "2026-10-22T06:00:00Z",
      server: "https://update.rezztor.com",
      plan: "paid",
      licence: "active",
      core: "2026.10.08",
      available: [
        listed("2026.10.21", { downloadable: true, security: true }),
        listed("2026.11.02", { bridge: true }),
      ],
      error: null,
      ...more,
    },
    ...status,
  };
}

describe("the list from Rezztor's update service", () => {
  it("lists every update oldest first and offers only the first one to a paid plan", async () => {
    await open(withList());
    expect(screen.getByText("Available updates")).toBeTruthy();
    const items = screen.getAllByRole("listitem").filter((item) => item.textContent?.includes("released"));
    expect(items).toHaveLength(2);
    expect(items[0]!.textContent).toContain("2026.10.21");
    expect(items[1]!.textContent).toContain("2026.11.02");
    expect(items[0]!.textContent).toContain("365.7 MB");
    expect(screen.getByText("security")).toBeTruthy();
    expect(screen.getByText("new core release")).toBeTruthy();
    expect(screen.getByText("Notes of 2026.11.02.")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Download and install" })).toHaveLength(1);
    expect(items[0]!.querySelector("button")?.textContent).toBe("Download and install");
    expect(screen.getByRole("button", { name: "Update to latest (2)" })).toBeTruthy();
    expect(screen.queryByText(/never looks for updates/)).toBeNull();
  });

  it("downloads the first update and installs it once the host has checked it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const start = withList();
    await open(start);
    api.downloadUpdate.mockResolvedValueOnce({ ok: true, request: "req-d" });
    api.requestUpdate.mockResolvedValue({ ok: true, request: "req-i" });
    api.fetchUpdateStatus
      .mockResolvedValueOnce({
        ...start,
        phase: "downloading",
        request: "req-d",
        download: { version: "2026.10.21", received: 191_718_400, size: 383_436_800 },
      })
      .mockResolvedValue({ ...start, phase: "checked", request: "req-d", package: { ...PACKAGE, version: "2026.10.21" } });
    fireEvent.click(screen.getByRole("button", { name: "Download and install" }));
    await waitFor(() => expect(api.downloadUpdate).toHaveBeenCalledWith("2026.10.21"));

    await vi.advanceTimersByTimeAsync(2000);
    await screen.findByText("Downloading 2026.10.21");
    expect(screen.getByText("182.8 MB of 365.7 MB")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Download of 2026.10.21" }).getAttribute("aria-valuenow")).toBe("50");

    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => expect(api.requestUpdate).toHaveBeenCalledWith("install"));
  });

  it("asks before updating to the latest, then follows the host through the list", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const start = withList();
    await open(start);
    api.requestUpdate.mockResolvedValueOnce({ ok: true, request: "req-all" });
    api.fetchUpdateStatus.mockResolvedValue({
      ...start,
      phase: "downloading",
      request: "req-all",
      progress: { done: 2, total: 7, current: "2026.11.02" },
      download: { version: "2026.11.02", received: 0, size: 383_436_800 },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update to latest (2)" }));
    await screen.findByText("Install all 2 updates?");
    const confirmButtons = screen.getAllByRole("button", { name: "Update to latest" });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]!);
    await waitFor(() => expect(api.requestUpdate).toHaveBeenCalledWith("update_all"));
    await vi.advanceTimersByTimeAsync(2000);
    await screen.findByText("Updating to the latest version");
    expect(screen.getByText("3 of 7")).toBeTruthy();
    expect(screen.getByText("2026.11.02")).toBeTruthy();
  });

  it("shows a free plan the list without the button", async () => {
    await open(withList({ plan: "free" }));
    expect(screen.getByText(/Upgrade to REZ-Pro to install from here/)).toBeTruthy();
    expect(screen.getByText("2026.10.21")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Download and install" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Update to latest/ })).toBeNull();
    // The file upload stays.
    expect(screen.getByRole("button", { name: "Choose file…" })).toBeTruthy();
  });

  it("tells a lapsed licence to renew", async () => {
    await open(withList({ plan: "free", licence: "lapsed" }));
    expect(screen.getByText(/Renew to receive updates/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Download and install" })).toBeNull();
  });

  it("explains how to register an installation the service does not know", async () => {
    await open(
      withList({
        available: [],
        error: { code: "unknown_installation", text: "The update service does not know this installation." },
      }),
    );
    expect(screen.getByText(/does not know this installation yet/)).toBeTruthy();
    expect(screen.getByText(/portal\.rezztor\.com/)).toBeTruthy();
    expect(screen.getAllByText("age1q7mtestk3xw9d").length).toBeGreaterThan(1);
    expect(screen.queryByText(/runs the latest version/)).toBeNull();
  });

  it("says a gap in the chain and an unreachable service in plain words", async () => {
    await open(withList({ error: { code: "chain_gap", text: "chain_gap" } }));
    expect(screen.getByText(/installed from here: updates go in strictly in order/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Download and install" })).toBeNull();
    cleanup();
    const unreached = "The update service at https://update.rezztor.com could not be reached (connection refused).";
    await open(withList({ plan: null, licence: null, core: null, available: [], error: { code: "unreachable", text: unreached } }));
    expect(screen.getByText(/outbound HTTPS to\s+it, through your proxy/)).toBeTruthy();
    expect(screen.queryByText(/outbound HTTPS to\s+update\.rezztor\.com/)).toBeNull();
    expect(screen.getByText(unreached)).toBeTruthy();
  });

  it("says every other failure of the service in plain words, not the host's code", async () => {
    const failed = (code: string) =>
      withList({ plan: null, licence: null, core: null, available: [], error: { code, text: `host says ${code}` } });
    const said: [string, RegExp][] = [
      ["not_a_release", /lab or development build/],
      ["not_installed", /not installed on this machine yet/],
      ["no_key", /has no update key/],
      ["bad_answer", /did not accept this installation's answer/],
      ["bad_challenge", /does not answer, so nothing was sent/],
      ["bad_service_answer", /cannot read\. Try again later/],
      ["service_busy", /busy\. Try again in an hour/],
      ["service", /answered with an error/],
    ];
    for (const [code, text] of said) {
      await open(failed(code));
      expect(screen.getByText(text)).toBeTruthy();
      expect(screen.queryByText(`host says ${code}`)).toBeNull();
      expect(screen.queryByText(/runs the latest version/)).toBeNull();
      cleanup();
    }
  });

  it("says a failed download or Update to latest on the list, and an upload's failure on the file card", async () => {
    await open(
      withList({}, { error: { code: "bad_download", text: "2 of 5 updates installed, then: the file was not the one listed." } }),
    );
    const list = screen.getByText("Available updates").closest("section")!;
    expect(list.textContent).toContain("2 of 5 updates installed, then: the file was not the one listed.");
    const file = screen.getByText("Install an update from a file").closest("section")!;
    expect(file.textContent).not.toContain("updates installed");
    cleanup();

    await open(withList({}, { error: { code: "upgrade_required", text: "x" } }));
    expect(screen.getByText("Available updates").closest("section")!.textContent).toContain("Upgrade to REZ-Pro, or download");
    cleanup();

    await open(withList({}, { error: { code: "bad_signature", text: "its signature does not match." } }));
    expect(screen.getByText("Install an update from a file").closest("section")!.textContent).toContain(
      "its signature does not match.",
    );
    expect(screen.getByText("Available updates").closest("section")!.textContent).not.toContain("signature");
  });

  it("says when this installation runs the latest version, and when it has never checked", async () => {
    await open(withList({ available: [] }));
    expect(screen.getByText("This installation runs the latest version.")).toBeTruthy();
    cleanup();
    await open({ ...IDLE, updates: null, daily_check: true });
    expect(screen.getByText("not yet")).toBeTruthy();
  });

  it("says when the next update needs a newer tool, instead of an empty list", async () => {
    const said = "Update 2026.11.02 needs a newer update tool. Install the bridge package by hand, or contact support.";
    await open(withList({ available: [], blocked: { version: "2026.11.02", reason: "format", needs: 5 } }));
    expect(screen.getByText(said)).toBeTruthy();
    expect(screen.queryByText(/runs the latest version/)).toBeNull();
    cleanup();
    await open(withList({}, { error: { code: "format", text: said } }));
    expect(screen.getByText("Available updates").closest("section")!.textContent).toContain(said);
  });

  it("checks now and says it is checking until the host has answered", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const start = withList();
    await open(start);
    api.requestUpdate.mockResolvedValueOnce({ ok: true, request: "req-r" });
    api.fetchUpdateStatus.mockResolvedValueOnce(start).mockResolvedValue({ ...start, request: "req-r" });
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await waitFor(() => expect(api.requestUpdate).toHaveBeenCalledWith("refresh"));
    await screen.findByRole("button", { name: "Checking…" });
    await vi.advanceTimersByTimeAsync(4000);
    await screen.findByRole("button", { name: "Check now" });
  });

  it("switches the daily check, and puts it back when the setting was not saved", async () => {
    await open(withList());
    const toggle = screen.getByRole("switch", { name: "Check for updates every day" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/^Asks Rezztor's update service once a day\./)).toBeTruthy();
    api.setDailyCheck.mockResolvedValueOnce({ ok: true, daily_check: false });
    fireEvent.click(toggle);
    await waitFor(() => expect(api.setDailyCheck).toHaveBeenCalledWith(false));
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"));

    api.setDailyCheck.mockResolvedValueOnce({ ok: false, error: "Not saved." });
    fireEvent.click(toggle);
    await screen.findByText("Not saved.");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });
});


const DEBIAN = {
  snapshot: "20261020T000000Z",
  reboot: true,
  packages: Array.from({ length: 12 }, (_, index) => ({ name: `pkg${index}`, from: "1", to: "2" })),
};

const REBOOT_NEEDED = {
  version: "2026.10.12",
  snapshot: "20261020T000000Z",
  applied: 12,
  packages: DEBIAN.packages,
  reboot_needed: true,
  error: null,
  at: "2026-10-12T14:06:00Z",
};

describe("Debian's updates in a package (ADR 0092)", () => {
  it("says what a package carries of Debian, in the list and on the checked package", async () => {
    await open(withList({ available: [listed("2026.10.21", { downloadable: true, os: { packages: 12, reboot: true } })] }));
    expect(screen.getByText("Debian security updates: 12 packages, restart of the appliance needed")).toBeTruthy();
    cleanup();
    await open({ ...IDLE, phase: "checked", package: { ...PACKAGE, os: { ...DEBIAN, reboot: false } } });
    expect(screen.getByText("Debian security updates: 12 packages")).toBeTruthy();
    cleanup();
    await open({ ...IDLE, phase: "checked", package: PACKAGE });
    expect(screen.queryByText(/Debian security updates/)).toBeNull();
  });

  it("offers a restart of the appliance after the install, asks first, and waits until it is back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await open({ ...IDLE, os: REBOOT_NEEDED });
    expect(screen.getByText("12 packages updated")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restart the appliance" }));
    await screen.findByText("Restart the appliance?");
    expect(screen.getByText(/away for a few minutes; agent runs in progress stop/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByText("Restart the appliance?")).toBeNull());
    expect(api.requestUpdate).not.toHaveBeenCalled();

    api.requestUpdate.mockResolvedValueOnce({ ok: true, request: "req-9" });
    api.fetchUpdateStatus
      .mockResolvedValueOnce({ ...IDLE, os: REBOOT_NEEDED, request: "req-9" })
      .mockRejectedValueOnce(new Error("502"))
      .mockResolvedValue({ ...IDLE, request: "req-9", os: { ...REBOOT_NEEDED, reboot_needed: false } });
    fireEvent.click(screen.getByRole("button", { name: "Restart the appliance" }));
    await screen.findByText("Restart the appliance?");
    const buttons = screen.getAllByRole("button", { name: "Restart the appliance" });
    fireEvent.click(buttons[buttons.length - 1]!);
    await screen.findByText("The appliance is restarting");
    expect(api.requestUpdate).toHaveBeenCalledWith("reboot");
    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByText("The appliance is restarting")).toBeTruthy();
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => expect(screen.getByText("This installation")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Restart the appliance" })).toBeNull();
  });

  it("says plainly when Debian's updates failed and the new version runs", async () => {
    const text = "The Debian updates could not be installed: apt-get upgrade: E: broken. Version 2026.10.12 runs. The next update carries them again.";
    await open({ ...IDLE, os: { ...REBOOT_NEEDED, applied: 0, reboot_needed: false, error: { code: "os_failed", text } } });
    expect(screen.getByText(text)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Restart the appliance" })).toBeNull();
  });

  it("says a refused restart on its card, not on the file card", async () => {
    await open({ ...IDLE, os: REBOOT_NEEDED, error: { code: "reboot", text: "The appliance could not be restarted: no." } });
    expect(screen.getAllByText("The appliance could not be restarted: no.")).toHaveLength(1);
  });

  it("says while Debian's updates go in that the new version already runs", async () => {
    await open({
      ...IDLE,
      phase: "restarting",
      package: PACKAGE,
      steps: [
        { id: "answer", text: "Waiting for the product to answer", state: "done" },
        { id: "system", text: "Installing Debian updates", state: "running" },
      ],
    });
    expect(screen.getByText("Installing Debian updates")).toBeTruthy();
    expect(screen.getByText(/Version 2026.10.12 runs/)).toBeTruthy();
  });
});
