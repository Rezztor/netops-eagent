// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { interactionOf } from "@/lib/transcript";
import { ConfirmProvider } from "@/components";
import { InteractionCard } from "../InteractionCard";

vi.mock("@/api/client", () => ({ apiRequest: vi.fn(), apiDownload: vi.fn() }));
vi.mock("@/lib/session", () => ({ useCan: () => true }));

const CODE = 'resource "azurerm_resource_group" "x" {\n  name     = "rg-x"\n  location = "uaenorth"\n}';
const card = (extra: Record<string, unknown> = {}) => ({
  interaction: "decision",
  interaction_id: "d_free",
  question: "Apply free code to rg-x?",
  options: JSON.stringify([{ label: "Approve", tone: "go" }, { label: "Reject", tone: "stop" }]),
  endpoint: "plugins/_ea_azure_change/azure_approve",
  payloads: JSON.stringify([{ decision: "approve" }, { decision: "reject" }]),
  tier: "change-high",
  allow_other: "false",
  multi: "false",
  ...extra,
});
const show = (kvps: Record<string, unknown>) =>
  render(<ConfirmProvider><InteractionCard interaction={interactionOf(kvps)!} live context="" onAnswer={vi.fn()} onCompose={vi.fn()} /></ConfirmProvider>);

afterEach(cleanup);

describe("the code block of a free-code change", () => {
  it("shows the whole code in a block that starts collapsed", () => {
    show(card({ code: CODE }));
    const block = screen.getByLabelText("Code");
    expect(block.textContent).toBe(CODE);
    expect(block.closest("details")?.open).toBe(false);
    expect(screen.getByText("Code (4 lines)")).toBeTruthy();
  });

  it("shows no block when the card carries no code", () => {
    show(card());
    expect(screen.queryByLabelText("Code")).toBeNull();
  });
});
