// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as azureApi from "@/api/azure";
import { AzureCost } from "../../inventory/cloud/AzureCost";

vi.mock("@/api/azure", async (importOriginal) => ({ ...(await importOriginal<typeof azureApi>()), getAzureCost: vi.fn() }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} } }),
  useCan: () => true,
}));

const cost = vi.mocked(azureApi.getAzureCost);

const answer = (over: Partial<azureApi.CostAnswer>): azureApi.CostAnswer => ({
  ok: true, question: "spend", headline: "", period: { from: "2026-09-01", to: "2026-09-30", timezone: "UTC" },
  totals: [{ currency: "USD", cost: 205 }],
  rows: [{ name: "Virtual Machines", cost: 170, currency: "USD" }, { name: "Storage", cost: 5, currency: "USD" }],
  rows_not_shown: 0, scope: { complete: true, covers: "2 of 2 stored subscriptions", not_included: [] },
  fetched_at: "2026-10-15T12:00:00+00:00", agreement: "MicrosoftCustomerAgreement",
  notes: ["Cost data can be up to 72 hours late.", "Credits are not deducted here.", "Quote these figures as given."], ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the Cost section", () => {
  it("shows last month and month to date by service with their periods, the as-of time and the lateness", async () => {
    cost.mockImplementation(async ({ timeframe }) =>
      timeframe === "last_month"
        ? answer({})
        : answer({ period: { from: "2026-10-01", to: "2026-10-15", timezone: "UTC" }, totals: [{ currency: "USD", cost: 40 }], rows: [{ name: "Virtual Machines", cost: 40, currency: "USD" }] }),
    );
    render(<AzureCost />);
    expect(await screen.findByText("Virtual Machines")).toBeTruthy();
    expect(screen.getByText("Last month (2026-09-01 to 2026-09-30 UTC)")).toBeTruthy();
    expect(screen.getByText("Month to date (2026-10-01 to 2026-10-15 UTC)")).toBeTruthy();
    expect(screen.getByText("170.00 USD")).toBeTruthy();
    expect(screen.getByText("205.00 USD")).toBeTruthy();
    expect(screen.getAllByText("40.00 USD")).toHaveLength(2); // the service and the total
    const note = screen.getByText(/Actual cost before tax/).textContent!;
    expect(note).toMatch(/72 hours late/);
    expect(note).toMatch(/Credits are not deducted/);
    expect(note).not.toMatch(/Quote these figures/);
    expect(screen.queryByText(/covers .* only/)).toBeNull();
  });

  it("says when the subscriptions it covers are not all of them", async () => {
    cost.mockResolvedValue(answer({ scope: { complete: false, covers: "1 of 2 stored subscriptions", not_included: [{ subscription: "Dev", reason: "it was not readable at the last pull" }] } }));
    render(<AzureCost />);
    expect((await screen.findAllByText(/covers 1 of 2 stored subscriptions only/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Dev \(it was not readable at the last pull\)/).length).toBeGreaterThan(0);
  });

  it("says why there is no cost instead of showing zeros", async () => {
    cost.mockResolvedValue({ ok: false, error: "The application's Azure role does not allow reading cost data here (HTTP 403)." } as azureApi.CostAnswer);
    render(<AzureCost />);
    expect((await screen.findAllByText(/does not allow reading cost data here/)).length).toBe(2);
    expect(screen.queryByText("Total")).toBeNull();
  });
});
