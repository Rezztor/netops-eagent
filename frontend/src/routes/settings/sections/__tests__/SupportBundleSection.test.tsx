// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { UpdateAvailable, UpdateStatus } from "@/api/updates";
import { SupportBundleSection } from "../SupportBundleSection";

const api = vi.hoisted(() => ({
  fetchUpdateStatus: vi.fn(),
  requestUpdate: vi.fn(),
  downloadSupportBundle: vi.fn(),
}));
vi.mock("@/api/updates", async () => {
  const actual = await vi.importActual("@/api/updates");
  return { ...(actual as object), ...api };
});
vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h" } }),
}));

const FILE = "rezztor-support-rezztor-eagent-20261015-120000.tar.gz";
const IDLE: UpdateAvailable = {
  available: true,
  phase: "idle",
  installed: { version: "2026.10.15", base: "2026.10.15", updated_at: null },
  update_key: "age1q7mtestk3xw9d",
  keys: ["release-2026a"],
  package: null,
  steps: [],
  error: null,
  result: null,
  history: [],
  request: null,
  at: "2026-10-15T12:00:00Z",
  bundles: [],
  bundle_error: null,
};

async function open(status: UpdateStatus) {
  api.fetchUpdateStatus.mockResolvedValue(status);
  render(<SupportBundleSection />);
  await waitFor(() => expect(api.fetchUpdateStatus).toHaveBeenCalled());
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("SupportBundleSection", () => {
  it("asks the host for a bundle, waits for it, and offers it for download", async () => {
    await open(IDLE);
    expect(await screen.findByText("None yet.")).toBeTruthy();
    expect(screen.getByText(/It reaches Rezztor only when you send it/)).toBeTruthy();

    api.requestUpdate.mockResolvedValue({ ok: true, request: "abcd1234abcd1234" });
    fireEvent.click(screen.getByRole("button", { name: "Make a bundle" }));
    await waitFor(() => expect(api.requestUpdate).toHaveBeenCalledWith("bundle"));
    expect(await screen.findByRole("button", { name: "Making the bundle…" })).toBeTruthy();

    api.fetchUpdateStatus.mockResolvedValue({
      ...IDLE,
      request: "abcd1234abcd1234",
      bundles: [{ file: FILE, size: 2_400_000, at: "2026-10-15T12:00:00Z" }],
    });
    expect(await screen.findByText(FILE, {}, { timeout: 4000 })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Make a bundle" })).toBeTruthy();

    api.downloadSupportBundle.mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() => expect(api.downloadSupportBundle).toHaveBeenCalledWith(FILE));
  });

  it("says why a bundle could not be made, and that a refused request was refused", async () => {
    await open({ ...IDLE, bundle_error: "The support bundle could not be made: No space left on device." });
    expect(await screen.findByText(/No space left on device/)).toBeTruthy();
    api.requestUpdate.mockResolvedValue({ ok: false, error: "The update service has not taken the last request yet." });
    fireEvent.click(screen.getByRole("button", { name: "Make a bundle" }));
    expect(await screen.findByText(/has not taken the last request yet/)).toBeTruthy();
  });

  it("offers nothing while an update is installed, nor where the host cannot make one", async () => {
    await open({ ...IDLE, phase: "installing" });
    expect((await screen.findByRole("button", { name: "Make a bundle" })).hasAttribute("disabled")).toBe(true);
    cleanup();
    const older: UpdateAvailable = { ...IDLE };
    delete older.bundles; // a host whose update tool has no bundle yet
    await open(older);
    expect(await screen.findByText(/makes no support bundle yet/)).toBeTruthy();
    cleanup();
    await open({ available: false });
    expect(await screen.findByText(/no host service to make one/)).toBeTruthy();
  });
});
