// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as azureApi from "@/api/azure";
import { ToastProvider } from "@/components";
import { AzureHistory } from "../../inventory/cloud/AzureHistory";
import { AzureSources } from "../../inventory/cloud/AzureSources";

vi.mock("@/api/azure", async (importOriginal) => ({
  ...(await importOriginal<typeof azureApi>()),
  getAzureStatus: vi.fn(),
  startAzurePull: vi.fn(),
  listAzureRuns: vi.fn(),
  listAzureRunChanges: vi.fn(),
}));
let can = true;
vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} } }),
  useCan: () => can,
}));

const m = {
  status: vi.mocked(azureApi.getAzureStatus),
  pull: vi.mocked(azureApi.startAzurePull),
  runs: vi.mocked(azureApi.listAzureRuns),
  changes: vi.mocked(azureApi.listAzureRunChanges),
};

const run = (over: Partial<azureApi.AzureRun> = {}): azureApi.AzureRun => ({
  id: "r1", status: "succeeded", started_at: "2026-09-26T02:00:00Z", finished_at: "2026-09-26T02:00:41Z",
  error: null, requested_by: "system:schedule",
  counts: {
    trigger: "schedule", duration_s: 41, resources: 1284, added: 12, changed: 21, removed: 4,
    subscriptions: { listed: 3, read: 3, failed: 0, skipped: 0 }, unknown_types: ["microsoft.foo/bar", "microsoft.foo/baz"],
  },
  ...over,
});
const status = (over: Partial<azureApi.AzureStatus> = {}): azureApi.AzureStatus => ({
  ok: true, enabled: true, configured: true, has_data: true, test_endpoints: false, running: null,
  last_run: run(), next_pull_at: "2026-10-03T02:00:00Z",
  as_of: { last_pull: run(), subscriptions: [] },
  credential: { kind: "certificate", expires_on: "2027-10-02", expires_in_days: 365 },
  verified_against_real_tenant: false, ...over,
});

function renderSources(onChanged = vi.fn()) {
  render(
    <MemoryRouter>
      <ToastProvider>
        <AzureSources canManage={can} onChanged={onChanged} />
      </ToastProvider>
    </MemoryRouter>,
  );
  return onChanged;
}
const label = () => screen.findByTestId("azure-status").then((el) => el.textContent);
const pullButton = () => screen.getByTestId("azure-pull") as HTMLButtonElement;

beforeEach(() => {
  can = true;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("the Azure card", () => {
  it("ready: last pull, result, found, next pull, credential, unknown types", async () => {
    m.status.mockResolvedValue(status());
    renderSources();
    expect(await label()).toBe("Ready");
    const card = screen.getByTestId("azure-sources").textContent!;
    expect(card).toMatch(/3 of 3 subscriptions read/);
    expect(card).toMatch(/1,284 resources · 12 added · 21 changed · 4 removed/);
    expect(card).toMatch(/by the schedule/);
    expect(card).toMatch(/Certificate · expires/);
    expect(card).toMatch(/2 · filed under Other/);
    expect(card).toMatch(/Not verified against a real tenant/);
    expect(pullButton().disabled).toBe(false);
  });

  it("running: shows progress, disables Pull now, polls every three seconds, then tells the screen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const running = run({ status: "running", finished_at: null, counts: { subscriptions: { listed: 3, read: 1, failed: 0, skipped: 0 } } });
    m.status.mockResolvedValueOnce(status({ running }));
    m.status.mockResolvedValue(status());
    const onChanged = renderSources();
    expect(await screen.findByTestId("azure-progress")).toBeTruthy();
    expect(screen.getByTestId("azure-progress").textContent).toMatch(/1 of 3 subscriptions done/);
    expect(pullButton().disabled).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    await waitFor(() => expect(screen.queryByTestId("azure-progress")).toBeNull());
    expect(m.status).toHaveBeenCalledTimes(2);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("partial: names the subscription that could not be read", async () => {
    const partial = run({
      status: "partial",
      counts: {
        ...run().counts,
        subscriptions: { listed: 3, read: 2, failed: 1, skipped: 0 },
        per_subscription: [{ id: "s3", name: "Sandbox", status: "failed", resources: 0, error: "Azure refused the read." }],
      },
    });
    m.status.mockResolvedValue(status({ last_run: partial, as_of: { last_pull: partial, subscriptions: [{ name: "Old", last_read_at: "2026-09-19T00:00:00Z", readable: false }] } }));
    renderSources();
    expect(await label()).toBe("Partial");
    const text = screen.getByTestId("azure-sources").textContent!;
    expect(text).toMatch(/2 of 3 subscriptions read/);
    expect(text).toMatch(/Sandbox: Azure refused the read/);
    expect(text).toMatch(/Old not readable since/);
  });

  it("failed: shows the plain reason and never offers the result as a count", async () => {
    m.status.mockResolvedValue(status({ last_run: run({ status: "failed", error: "The credential has expired.", counts: null }), running: null }));
    renderSources();
    expect(await label()).toBe("Last pull failed");
    expect(screen.getByText("The credential has expired.")).toBeTruthy();
  });

  it("not set up: points to Settings and cannot pull", async () => {
    m.status.mockResolvedValue(status({ configured: false, has_data: false, last_run: null, next_pull_at: null, credential: { kind: "", expires_on: "", expires_in_days: null } }));
    renderSources();
    expect(await label()).toBe("Not set up");
    expect(screen.getByTestId("azure-not-set-up").textContent).toMatch(/Settings, Azure/);
    expect(pullButton().disabled).toBe(true);
  });

  it("disabled: says it is the picture from the last pull and cannot pull", async () => {
    m.status.mockResolvedValue(status({ enabled: false }));
    renderSources();
    expect(await label()).toBe("Disabled");
    expect(screen.getByText("Azure is off. This is the picture from the last pull.")).toBeTruthy();
    expect(pullButton().disabled).toBe(true);
  });

  it("test endpoints: says it is not reading Microsoft", async () => {
    m.status.mockResolvedValue(status({ test_endpoints: true }));
    renderSources();
    expect(await screen.findByTestId("azure-test-endpoints")).toBeTruthy();
  });

  it("warns when the credential expires within 30 days", async () => {
    m.status.mockResolvedValue(status({ credential: { kind: "secret", expires_on: "2026-10-20", expires_in_days: 18 } }));
    renderSources();
    expect((await screen.findByTestId("azure-expiry")).textContent).toMatch(/expires in 18 days/);
  });

  it("Pull now is disabled without inventory.manage, and shows a refusal when asked", async () => {
    can = false;
    m.status.mockResolvedValue(status());
    renderSources();
    await label();
    expect(pullButton().disabled).toBe(true);
    cleanup();

    can = true;
    m.pull.mockResolvedValue({ ok: false, reason: "too_soon", error: "The last pull finished 3 minutes ago." });
    renderSources();
    await label();
    fireEvent.click(pullButton());
    expect(await screen.findByText("The last pull finished 3 minutes ago.")).toBeTruthy();
  });
});

describe("History", () => {
  it("lists the runs and a run's changes with old and new values", async () => {
    m.runs.mockResolvedValue({
      ok: true,
      runs: [run(), run({ id: "r2", status: "partial", requested_by: "engineer1", counts: { trigger: "chat", added: 2, changed: 5, removed: 0 } })],
    });
    m.changes.mockResolvedValue({
      ok: true, total: 2,
      changes: [
        { id: "c1", run_id: "r2", at: "2026-09-26T02:01:00Z", change: "changed", fields: { "props.https_only": { old: false, new: true } },
          resource: { id: "res1", name: "app-portal-prod", type: "microsoft.web/sites", label: "Web app", category: "web", subscription: "Production", resource_group: "rg-portal-prod", removed_at: null } },
        { id: "c2", run_id: "r2", at: "2026-09-26T02:01:00Z", change: "removed", fields: {},
          resource: { id: "res2", name: "app-old", type: "microsoft.web/sites", label: "Web app", category: "web", subscription: "Production", resource_group: "rg-portal-prod", removed_at: "2026-09-26T02:01:00Z" } },
      ],
    });
    const onOpen = vi.fn();
    render(<ToastProvider><AzureHistory onOpenResource={onOpen} /></ToastProvider>);

    const rows = await screen.findAllByTestId("azure-run");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toMatch(/the schedule/);
    expect(rows[1]!.textContent).toMatch(/engineer1, chat/);
    expect(rows[1]!.textContent).toMatch(/partial/);

    fireEvent.click(rows[1]!.querySelector("button")!);
    const change = (await screen.findAllByTestId("azure-change"))[0]!;
    expect(m.changes).toHaveBeenCalledWith("r2", { limit: 50, offset: 0 });
    expect(change.textContent).toMatch(/props\.https_only: false → true/);
    expect(screen.getAllByTestId("azure-change")[1]!.textContent).toMatch(/removed/);

    fireEvent.click(screen.getByText("app-portal-prod"));
    expect(onOpen).toHaveBeenCalledWith("res1");
  });

  it("says so when no pull has run", async () => {
    m.runs.mockResolvedValue({ ok: true, runs: [] });
    render(<ToastProvider><AzureHistory onOpenResource={vi.fn()} /></ToastProvider>);
    expect(await screen.findByText("No Azure pull has run yet")).toBeTruthy();
  });
});
