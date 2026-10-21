// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ResourceSignals } from "@/api/azure";
import { AzureSignals } from "../../inventory/cloud/AzureSignals";

vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} } }),
}));

afterEach(cleanup);

const none: ResourceSignals = { read_at: "2026-10-03T02:00:00Z", advisor: [], policy: [], health: [], change: [] };

describe("what Azure says on a resource page", () => {
  it("says so when the findings were never read, and when Azure had nothing to say", () => {
    const { rerender } = render(<AzureSignals signals={{ ...none, read_at: null }} />);
    expect(screen.getByText(/have not been read yet/)).toBeTruthy();
    rerender(<AzureSignals signals={none} />);
    expect(screen.getByText(/Azure reported nothing about this resource/)).toBeTruthy();
  });

  it("shows health, advice, policy and change records as Azure gave them, with who changed it", () => {
    render(
      <AzureSignals
        signals={{
          ...none,
          health: [{ at: "2026-10-03T00:35:00Z", state: "Unavailable", previous_state: "Available" }, { at: null, annotation: "HostRebooted", reason: "Unexpected host failure" }],
          advisor: [{ at: null, impact: "High", category: "Cost", problem: "Right-size or shutdown underutilized virtual machines", current_sku: "D4s_v5", target_sku: "D2s_v5", savings: "86.4", currency: "USD", annual_savings: "1036.8" }],
          policy: [{ at: null, state: "NonCompliant", assignment: "require-https", action: "audit" }],
          change: [{
            at: "2026-10-02T08:30:00Z", change_type: "Update", changed_by: "alice@example.com", changed_by_type: "User", client: "Azure Portal",
            changes: [
              { path: "properties.httpsOnly", kind: "Update", old: "False", new: "True" },
              { path: "properties.siteConfig.appSettings[].value", kind: "Update", hidden: true },
            ],
          }],
        }}
      />,
    );
    expect(screen.getByText("Unavailable")).toBeTruthy();
    expect(screen.getByText(/Unexpected host failure/)).toBeTruthy();
    expect(screen.getByText(/D4s_v5 to D2s_v5/)).toBeTruthy();
    expect(screen.getByText(/Microsoft's estimate at retail rates: 86.4 USD \(1036.8 a year\)/)).toBeTruthy();
    expect(screen.getByText("require-https")).toBeTruthy();
    expect(screen.getByText(/alice@example.com \(User\) via Azure Portal/)).toBeTruthy();
    expect(screen.getByText("properties.httpsOnly: False became True")).toBeTruthy();
    expect(screen.getByText("properties.siteConfig.appSettings[].value: changed (the value is not stored)")).toBeTruthy();
  });

  it("renders nothing when the page has no signals field (an older answer)", () => {
    const { container } = render(<AzureSignals />);
    expect(container.textContent).toBe("");
  });
});
