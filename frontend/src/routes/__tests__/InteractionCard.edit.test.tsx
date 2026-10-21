// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/api/client";
import { interactionOf } from "@/lib/transcript";
import { ConfirmProvider } from "@/components";
import { InteractionCard } from "../InteractionCard";

vi.mock("@/api/client", () => ({ apiRequest: vi.fn() }));
vi.mock("@/lib/session", () => ({ useCan: () => true }));

const kvps = (hash: string, commands: string[]) => ({
  interaction: "decision",
  interaction_id: "d_1",
  question: "Read `reads/show` on lab-sonic?",
  options: JSON.stringify([{ label: "Approve", tone: "go" }, { label: "Reject", tone: "stop" }]),
  endpoint: "plugins/_ea_ansible/change_approve",
  payloads: JSON.stringify([
    { change_id: "c1", expected_hash: hash, decision: "approve" },
    { change_id: "c1", expected_hash: hash, decision: "reject" },
  ]),
  commands: JSON.stringify(commands),
  editable: "true",
  edit_endpoint: "plugins/_ea_ansible/read_edit",
  edit_payload: JSON.stringify({ change_id: "c1", expected_hash: hash }),
  allow_other: "false",
  multi: "false",
});

afterEach(() => {
  cleanup();
  vi.mocked(apiRequest).mockReset();
});

/** The card also reads its change's state; that read says "still awaiting", and every other call gets the next answer. */
function answers(...queue: unknown[]) {
  vi.mocked(apiRequest).mockImplementation(async (endpoint: string) =>
    endpoint === "plugins/_ea_ansible/changes_list" ? { ok: true, change: { status: "awaiting_approval" } } : queue.shift(),
  );
}

describe("editing a read on its card", () => {
  it("sends the commands, shows the rebuilt card, and approves against the new hash", async () => {
    const mock = vi.mocked(apiRequest);
    answers({ ok: true, card: kvps("h2", ["# lab-sonic (sonic_community)", "show arp"]) }, { ok: true });
    const onAnswer = vi.fn();
    render(<ConfirmProvider><InteractionCard
        interaction={interactionOf(kvps("h1", ["# lab-sonic (sonic_community)", "show vlan brief"]))!}
        live
        context=""
        onAnswer={onAnswer}
        onCompose={vi.fn()}
      /></ConfirmProvider>);

    fireEvent.click(screen.getByText("Edit commands"));
    const box = screen.getByLabelText("Commands to run") as HTMLTextAreaElement;
    expect(box.value).toBe("show vlan brief"); // the "# device" header is not editable
    fireEvent.change(box, { target: { value: "show arp\n" } });
    fireEvent.click(screen.getByText("Save commands"));

    await waitFor(() => expect(screen.getByText(/show arp/)).toBeTruthy());
    expect(mock).toHaveBeenCalledWith("plugins/_ea_ansible/read_edit", {
      body: { change_id: "c1", expected_hash: "h1", commands: ["show arp"] },
    });

    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(onAnswer).toHaveBeenCalled());
    expect(mock).toHaveBeenCalledWith("plugins/_ea_ansible/change_approve", {
      body: { change_id: "c1", expected_hash: "h2", decision: "approve" },
    });
  });

  it("shows the server's refusal and keeps the draft", async () => {
    answers({ ok: false, error: "No pipes" });
    render(<ConfirmProvider><InteractionCard
        interaction={interactionOf(kvps("h1", ["show vlan brief"]))!}
        live
        context=""
        onAnswer={vi.fn()}
        onCompose={vi.fn()}
      /></ConfirmProvider>);
    fireEvent.click(screen.getByText("Edit commands"));
    fireEvent.change(screen.getByLabelText("Commands to run"), { target: { value: "show vlan brief | grep 10" } });
    fireEvent.click(screen.getByText("Save commands"));
    await waitFor(() => expect(screen.getByText("No pipes")).toBeTruthy());
    expect((screen.getByLabelText("Commands to run") as HTMLTextAreaElement).value).toContain("grep");
  });
});
