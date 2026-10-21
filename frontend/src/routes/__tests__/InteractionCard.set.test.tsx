// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiDownload, apiRequest } from "@/api/client";
import { interactionOf } from "@/lib/transcript";
import { ConfirmProvider } from "@/components";
import { InteractionCard } from "../InteractionCard";

vi.mock("@/api/client", () => ({ apiRequest: vi.fn(), apiDownload: vi.fn() }));
vi.mock("@/lib/session", () => ({ useCan: () => true }));

const STATE = "plugins/_ea_azure_change/azure_changes";
const DOCUMENT = "plugins/_ea_azure_change/azure_document";
const TITLES = ["Resource group rg-hub", "Network vnet-hub", "Network vnet-spoke1", "Subnet snet-app", "Peering hub to spoke1", "Peering spoke1 to hub"];
const steps = TITLES.map((title, i) => ({
  n: i + 1,
  title,
  adds: 1,
  lines: [`+ azurerm_thing.t${i + 1} name-${i + 1}`, `~ azurerm_other.o${i + 1}`, `- tags: {}`, "... 4 more lines in the change document"],
}));

const setCard = (extra: Record<string, unknown> = {}) => ({
  interaction: "decision",
  interaction_id: "d_set",
  question: "Apply the 6 steps of this change set?",
  consequence: "6 steps. Adds 6, changes 0, removes 0. About 0.0 USD a month at list price.",
  options: JSON.stringify([{ label: "Approve", tone: "go" }, { label: "Reject", tone: "stop" }]),
  endpoint: "plugins/_ea_azure_change/azure_approve",
  payloads: JSON.stringify([
    { change_id: "9d02b7c4aa", expected_hash: "h1", decision: "approve" },
    { change_id: "9d02b7c4aa", expected_hash: "h1", decision: "reject" },
  ]),
  reference: JSON.stringify({ label: "CR or SR number", pattern: "(CR|SR)-?[0-9]{4,10}", example: "CR-123456" }),
  documents: JSON.stringify([{ label: "Change document (PDF)", payload: { change_id: "9d02b7c4aa", version: "issued", format: "pdf" } }]),
  document_endpoint: DOCUMENT,
  steps: JSON.stringify(steps),
  tier: "change-low",
  allow_other: "false",
  multi: "false",
  ...extra,
});

const serve = (state: Record<string, unknown>) =>
  vi.mocked(apiRequest).mockImplementation(async (endpoint: string) =>
    endpoint === STATE ? { ok: true, change: { status: "awaiting_approval", downloaded: false, reference: "", ...state } } : { ok: true },
  );
const show = (kvps: Record<string, unknown>) =>
  render(<ConfirmProvider><InteractionCard interaction={interactionOf(kvps)!} live context="" onAnswer={vi.fn()} onCompose={vi.fn()} /></ConfirmProvider>);
const approve = () => screen.getByText("Approve").closest("button") as HTMLButtonElement;
const states = (...list: string[]) => list.map((status, i) => ({ n: i + 1, title: TITLES[i]!, status }));

afterEach(() => {
  cleanup();
  vi.mocked(apiRequest).mockReset();
  vi.mocked(apiDownload).mockReset();
});

describe("a change set on its card", () => {
  it("lists every step in order, each closed, and opens one to its plan lines coloured as a diff", async () => {
    serve({});
    const { container } = show(setCard());
    const blocks = [...container.querySelectorAll("details")];
    expect(blocks).toHaveLength(6);
    expect(blocks.map((b) => b.querySelector("summary")!.textContent)).toEqual(TITLES.map((t, i) => `${i + 1}. ${t} · adds 1`));
    expect(blocks.every((b) => !b.open)).toBe(true);
    const plan = within(blocks[2]!).getByLabelText("Step 3 plan");
    expect(plan.textContent).toContain("+ azurerm_thing.t3 name-3");
    expect(plan.textContent).toContain("... 4 more lines in the change document");
    expect(screen.getByText("+ azurerm_thing.t3 name-3").className).toMatch(/diffAdd/);
    expect(screen.getByText("~ azurerm_other.o3").className).toMatch(/diffUpd/);
    expect(screen.getAllByText("- tags: {}")[2]!.className).toMatch(/diffDel/);
    await waitFor(() => expect(apiRequest).toHaveBeenCalled());
  });

  it("keeps the three steps for the engineer and Approve off until the document and the number are in", async () => {
    serve({ downloaded: false });
    vi.mocked(apiDownload).mockResolvedValue(undefined);
    const { container } = show(setCard());
    const numbered = [...container.querySelectorAll("p")].map((p) => p.textContent).filter((t) => /^[123] (Download|Enter|Press)/.test(t ?? ""));
    expect(numbered).toEqual(["1 Download the change document", "2 Enter the CR or SR number", "3 Press Approve"]);
    fireEvent.change(screen.getByLabelText(/CR or SR number/), { target: { value: "CR-0011" } });
    expect(approve().disabled).toBe(true);
    expect(screen.getByText(/Approve is off until you download the change document \(step 1\)/)).toBeTruthy();
    serve({ downloaded: true });
    fireEvent.click(screen.getByText("Change document (PDF)"));
    await waitFor(() => expect(approve().disabled).toBe(false));
  });

  it("shows no state chip while the set awaits approval", async () => {
    serve({ steps: states("planned", "planned", "planned", "planned", "planned", "planned") });
    show(setCard());
    await waitFor(() => expect(apiRequest).toHaveBeenCalled());
    expect(document.querySelector("[class*=cardStepState]")).toBeNull();
  });

  it("after a stop says how far it got and puts a chip on each step: applied, the one that stopped, never ran", async () => {
    serve({
      status: "failed",
      reference: "CR-0011",
      approved_by_name: "admin",
      steps: states("applied", "applied", "failed", "not_run", "not_run", "not_run"),
    });
    const { container } = show(setCard());
    await screen.findByText(/^Applied 2 of 6\. Stopped at step 3\. Approved with CR-0011 by admin/);
    const chips = [...container.querySelectorAll("summary")].map((s) => s.querySelector("span")?.textContent);
    expect(chips).toEqual(["applied", "applied", "failed", "not run", "not run", "not run"]);
    expect(container.querySelectorAll("summary")[2]!.querySelector("span")!.className).toMatch(/failed/);
    expect(screen.queryByText("Approve")).toBeNull();
  });

  it("says every step applied when they all did, and a plan that changed at step 1 as stopped with none applied", async () => {
    serve({ status: "applied", steps: states("applied", "applied", "applied", "applied", "applied", "applied") });
    show(setCard());
    await screen.findByText(/^Applied all 6 steps\./);
    cleanup();
    serve({ status: "superseded", steps: states("superseded", "not_run", "not_run", "not_run", "not_run", "not_run") });
    show(setCard());
    await screen.findByText(/^Applied 0 of 6\. Stopped at step 1\./);
  });

  it("keeps asking while the set applies, so the chips turn without a reload", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      serve({ status: "applying", approved_by_name: "admin", steps: states("applied", "applying", "planned", "planned", "planned", "planned") });
      const { container } = show(setCard());
      await screen.findByText(/Applying now\. Applied 1 of 6\./);
      expect([...container.querySelectorAll("summary")].map((s) => s.querySelector("span")?.textContent)).toEqual(["applied", "applying", undefined, undefined, undefined, undefined]);
      serve({ status: "applied", steps: states("applied", "applied", "applied", "applied", "applied", "applied") });
      vi.advanceTimersByTime(10_000);
      await screen.findByText(/^Applied all 6 steps\./);
    } finally {
      vi.useRealTimers();
    }
  });

  it("is not a set card when its steps are malformed: all of what is approved is shown or none of it", () => {
    for (const bad of ["not json", JSON.stringify([{ n: 2, title: "x", lines: [] }]), JSON.stringify([{ n: 1, title: "", lines: [] }]), JSON.stringify([steps[0], { n: 3, title: "x", lines: [] }])]) {
      expect(interactionOf(setCard({ steps: bad }))).toBeNull();
    }
    expect(interactionOf(setCard({ interaction: "question", endpoint: undefined, payloads: undefined }))!.steps).toBeUndefined();
  });
});
