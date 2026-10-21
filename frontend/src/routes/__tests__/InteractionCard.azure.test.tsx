// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiDownload, apiRequest } from "@/api/client";
import { interactionOf } from "@/lib/transcript";
import { ConfirmProvider } from "@/components";
import { InteractionCard } from "../InteractionCard";

vi.mock("@/api/client", () => ({ apiRequest: vi.fn(), apiDownload: vi.fn() }));
vi.mock("@/lib/session", () => ({ useCan: () => true }));

const DOCUMENT = "plugins/_ea_azure_change/azure_document";
const docs = [
  { label: "Change document (PDF)", payload: { change_id: "7c41e2a9bb", version: "issued", format: "pdf" } },
  { label: "Word copy (.docx)", payload: { change_id: "7c41e2a9bb", version: "issued", format: "docx" } },
];

const change = (extra: Record<string, unknown> = {}) => ({
  interaction: "decision",
  interaction_id: "d_az",
  question: "Create resource group rg-web?",
  options: JSON.stringify([{ label: "Approve", tone: "go" }, { label: "Reject", tone: "stop" }]),
  endpoint: "plugins/_ea_azure_change/azure_approve",
  payloads: JSON.stringify([
    { change_id: "7c41e2a9bb", expected_hash: "h1", decision: "approve" },
    { change_id: "7c41e2a9bb", expected_hash: "h1", decision: "reject" },
  ]),
  reference: JSON.stringify({ label: "CR or SR number", pattern: "(CR|SR)-?[0-9]{4,10}", example: "CR-123456" }),
  documents: JSON.stringify(docs),
  document_endpoint: DOCUMENT,
  details: JSON.stringify([{ label: "Change document", value: "7c41e2a9, digest 3fa9" }]),
  checks: JSON.stringify([
    { label: "Nothing is deleted, stopped or restarted", ok: true },
    { label: "Advice: no zone", ok: true, level: "warn", detail: "one zone" },
  ]),
  diff: JSON.stringify(["+ azurerm_resource_group.main  rg-web", "~ azurerm_subnet.a", "-   tags: {}", "+   tags: {env: prod}"]),
  tier: "change-low",
  allow_other: "false",
  multi: "false",
  ...extra,
});

const show = (kvps: Record<string, unknown>) =>
  render(<ConfirmProvider><InteractionCard interaction={interactionOf(kvps)!} live context="" onAnswer={vi.fn()} onCompose={vi.fn()} /></ConfirmProvider>);
const STATE = "plugins/_ea_azure_change/azure_changes";
/** The server's answer about the change, and ok for everything else (the decision itself). */
const serve = (state: Record<string, unknown> | null) =>
  vi.mocked(apiRequest).mockImplementation(async (endpoint: string) =>
    endpoint === STATE ? (state ? { ok: true, change: { status: "awaiting_approval", downloaded: false, reference: "", ...state } } : { ok: false }) : { ok: true },
  );
const decisionCalls = () => vi.mocked(apiRequest).mock.calls.filter(([endpoint]) => endpoint !== STATE);
const approve = () => screen.getByText("Approve").closest("button") as HTMLButtonElement;

afterEach(() => {
  cleanup();
  vi.mocked(apiRequest).mockReset();
  vi.mocked(apiDownload).mockReset();
});

describe("an Azure change on its card", () => {
  it("keeps Approve disabled with no number or a malformed one, and Reject needs none", async () => {
    serve({ downloaded: true });
    show(change());
    await waitFor(() => expect(apiRequest).toHaveBeenCalled());
    expect(approve().disabled).toBe(true);
    expect((screen.getByText("Reject").closest("button") as HTMLButtonElement).disabled).toBe(false);
    const box = screen.getByLabelText(/CR or SR number/);
    for (const bad of ["cr 12", "CR-12", "CR-12345678901", "TICKET-1234", " ", "x(CR-1234)", "CR-1234\nCR-1234"]) {
      fireEvent.change(box, { target: { value: bad } });
      expect(approve().disabled).toBe(true);
    }
  });

  it("enables Approve on a full match, case-insensitive, and sends the trimmed number as reference", async () => {
    serve({ downloaded: true });
    show(change());
    fireEvent.change(screen.getByLabelText(/CR or SR number/), { target: { value: " cr-123456 " } });
    await waitFor(() => expect(approve().disabled).toBe(false));
    fireEvent.click(approve());
    await waitFor(() => expect(decisionCalls()).toHaveLength(1));
    expect(apiRequest).toHaveBeenCalledWith("plugins/_ea_azure_change/azure_approve", {
      body: { change_id: "7c41e2a9bb", expected_hash: "h1", decision: "approve", reference: "cr-123456" },
    });
  });

  it("says in the chat that the approval was made on the card, with the number, not a bare word", async () => {
    serve({ downloaded: true });
    const onAnswer = vi.fn();
    render(<ConfirmProvider><InteractionCard interaction={interactionOf(change())!} live context="" onAnswer={onAnswer} onCompose={vi.fn()} /></ConfirmProvider>);
    fireEvent.change(screen.getByLabelText(/CR or SR number/), { target: { value: "CR-0011" } });
    await waitFor(() => expect(approve().disabled).toBe(false));
    fireEvent.click(approve());
    await waitFor(() => expect(onAnswer).toHaveBeenCalled());
    expect(onAnswer.mock.calls[0]![0]).toBe("Approved on the card with CR-0011.");
  });

  it("keeps Approve off when the pattern cannot be read, rather than on", async () => {
    serve({ downloaded: true });
    show(change({ reference: JSON.stringify({ label: "Number", pattern: "(unclosed", example: "" }) }));
    fireEvent.change(screen.getByLabelText(/Number/), { target: { value: "(unclosed" } });
    await waitFor(() => expect(apiRequest).toHaveBeenCalled());
    expect(approve().disabled).toBe(true);
  });

  it("sends no reference with Reject, which needs neither the document nor the number", async () => {
    serve({ downloaded: false });
    show(change());
    fireEvent.click(screen.getByText("Reject"));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(decisionCalls()).toHaveLength(1));
    expect(decisionCalls()[0]![1]).toEqual({
      body: { change_id: "7c41e2a9bb", expected_hash: "h1", decision: "reject" },
    });
  });

  it("downloads each document through the fixed endpoint and shows an advice check as amber", async () => {
    vi.mocked(apiDownload).mockResolvedValue(undefined);
    show(change());
    fireEvent.click(screen.getByText("Word copy (.docx)"));
    await waitFor(() => expect(apiDownload).toHaveBeenCalled());
    expect(apiDownload).toHaveBeenCalledWith(DOCUMENT, {
      method: "POST",
      body: docs[1]!.payload,
      filename: "azure-issued-7c41e2a9.docx",
    });
    await screen.findByText("Word copy (.docx) · downloaded");
    expect(screen.getByText(/Advice: no zone — one zone/).className).toMatch(/Warn/);
    expect(screen.getByLabelText("Diff").textContent).toContain("~ azurerm_subnet.a");
  });

  it("shows a refused download as the server wrote it", async () => {
    vi.mocked(apiDownload).mockRejectedValue(new Error("The stored file no longer matches its record."));
    show(change());
    fireEvent.click(screen.getByText("Change document (PDF)"));
    await screen.findByText("The stored file no longer matches its record.");
  });

  it("offers no document button when the card names another endpoint", () => {
    show(change({ document_endpoint: "users_create" }));
    expect(screen.queryByText("Change document (PDF)")).toBeNull();
  });
});

describe("the order on an Azure change card, and what the server says about the change", () => {
  it("shows the three steps in place and keeps Approve off, saying why, until the document is downloaded", async () => {
    serve({ downloaded: false });
    vi.mocked(apiDownload).mockResolvedValue(undefined);
    const { container } = show(change());
    const steps = [...container.querySelectorAll("p")].map((p) => p.textContent).filter((t) => /^\d /.test(t ?? ""));
    expect(steps).toEqual(["1 Download the change document", "2 Enter the CR or SR number", "3 Press Approve"]);
    fireEvent.change(screen.getByLabelText(/CR or SR number/), { target: { value: "CR-0011" } });
    expect(approve().disabled).toBe(true);
    expect(screen.getByText(/Approve is off until you download the change document \(step 1\)/)).toBeTruthy();
    // the server learns of the download, the card follows it
    serve({ downloaded: true });
    fireEvent.click(screen.getByText("Change document (PDF)"));
    await waitFor(() => expect(approve().disabled).toBe(false));
    expect(screen.queryByText(/Approve is off/)).toBeNull();
  });

  it("asks which step is missing: the number, once the document is in", async () => {
    serve({ downloaded: true });
    show(change());
    await screen.findByText(/Approve is off until the number matches the format \(step 2\)/);
    fireEvent.change(screen.getByLabelText(/CR or SR number/), { target: { value: "CR-0011" } });
    await waitFor(() => expect(approve().disabled).toBe(false));
  });

  it("knows a document was downloaded before this page was loaded", async () => {
    serve({ downloaded: true });
    show(change());
    await waitFor(() => expect(screen.getByText(/Download the change document ✓/)).toBeTruthy());
    expect(apiRequest).toHaveBeenCalledWith(STATE, { body: { change_id: "7c41e2a9bb" } });
  });

  it("still lets a download count when the state cannot be read", async () => {
    serve(null);
    vi.mocked(apiDownload).mockResolvedValue(undefined);
    show(change());
    fireEvent.change(screen.getByLabelText(/CR or SR number/), { target: { value: "CR-0011" } });
    expect(approve().disabled).toBe(true);
    fireEvent.click(screen.getByText("Change document (PDF)"));
    await waitFor(() => expect(approve().disabled).toBe(false));
  });

  it.each([
    [{ status: "approved", reference: "CR-0011", approved_by_name: "admin", approved_at: 1759510920 }, /Approved with CR-0011 by admin, /],
    [{ status: "applied", reference: "CR-0011", approved_by_name: "admin" }, /^Applied\. Approved with CR-0011 by admin/],
    [{ status: "rejected" }, /Rejected/],
    [{ status: "superseded" }, /Superseded: a newer plan was made/],
    [{ status: "cancelled" }, /Cancelled/],
    [{ status: "expired" }, /Expired/],
  ])("shows what happened to a change that is no longer awaiting approval, with no live buttons: %j", async (state, text) => {
    serve(state);
    show(change());
    await screen.findByText(text);
    expect(screen.queryByText("Approve")).toBeNull();
    expect(screen.queryByText("Reject")).toBeNull();
    expect(screen.queryByLabelText(/CR or SR number/)).toBeNull();
    expect(screen.queryByText("Change document (PDF)")).toBeNull();
  });

  it("goes to the approved line after Approve, from the server's answer", async () => {
    serve({ downloaded: true });
    show(change());
    fireEvent.change(screen.getByLabelText(/CR or SR number/), { target: { value: "CR-0011" } });
    await waitFor(() => expect(approve().disabled).toBe(false));
    serve({ status: "approved", reference: "CR-0011", approved_by_name: "admin", approved_at: 1759510920 });
    fireEvent.click(approve());
    await screen.findByText(/Approved with CR-0011 by admin/);
  });
});

describe("a suggestion", () => {
  const suggestion = {
    interaction: "question",
    interaction_id: "q_s",
    question: "Suggestion: remove the idle disk",
    options: JSON.stringify([{ label: "I will do this myself" }, { label: "Not now" }]),
    tier: "suggestion",
    commands: JSON.stringify(["1. Snapshot the disk"]),
    documents: JSON.stringify([
      { label: "Runbook (PDF)", payload: { change_id: "7c41e2a9bb", version: "runbook", format: "pdf" } },
    ]),
    document_endpoint: DOCUMENT,
    allow_other: "true",
    multi: "false",
  };

  it("has the runbook and an answer, and nothing that executes", async () => {
    const onAnswer = vi.fn();
    render(<ConfirmProvider><InteractionCard interaction={interactionOf(suggestion)!} live context="" onAnswer={onAnswer} onCompose={vi.fn()} /></ConfirmProvider>);
    expect(screen.getByText("Suggestion")).toBeTruthy();
    expect(screen.getByText("1. Snapshot the disk")).toBeTruthy();
    expect(screen.getByText("Runbook (PDF)")).toBeTruthy();
    expect(screen.queryByText("Approve")).toBeNull();
    fireEvent.click(screen.getByText("I will do this myself"));
    expect(onAnswer).toHaveBeenCalledWith("I will do this myself", "q_s");
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("is not a decision even if it names an endpoint", () => {
    expect(interactionOf({ ...suggestion, endpoint: "plugins/_ea_azure_change/azure_approve" })?.endpoint).toBeUndefined();
  });
});
