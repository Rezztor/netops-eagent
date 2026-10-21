// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as consoleApi from "@/api/networkConsole";
import { interactionOf } from "@/lib/transcript";
import { ConfirmProvider } from "@/components";
import { InteractionCard } from "../InteractionCard";

vi.mock("@/api/networkConsole", async (importOriginal) => ({
  ...(await importOriginal<typeof consoleApi>()),
  saveCredential: vi.fn(),
}));

let permissions = ["plugins.manage"];
vi.mock("@/lib/session", () => ({
  useCan: (...required: string[]) => required.every((p) => permissions.includes(p)),
}));

const SECRET = "Zq9-fake-Pass-1234";
const interaction = interactionOf({
  interaction: "secret_form",
  interaction_id: "s_abc",
  question: "Enter the credential 'lab-sonic' securely",
  endpoint: "plugins/_ea_ansible/network_inventory",
  credential_name: "lab-sonic",
  credential_kind: "password",
  username: "",
  note: "",
})!;

function show(onAnswer = vi.fn()) {
  render(<ConfirmProvider><InteractionCard interaction={interaction} live context="" onAnswer={onAnswer} onCompose={vi.fn()} /></ConfirmProvider>);
  return onAnswer;
}

beforeEach(() => {
  permissions = ["plugins.manage"];
  vi.mocked(consoleApi.saveCredential).mockResolvedValue({
    ok: true,
    credential: { name: "lab-sonic", kind: "password", username: "", fingerprint: "ab12cd", updated_at: 1 },
  });
});
afterEach(() => cleanup());

describe("secret form card", () => {
  it("renders a masked, read-only-named form", () => {
    show();
    const secret = screen.getByLabelText("Secret") as HTMLInputElement;
    expect(secret.type).toBe("password");
    expect(secret.autocomplete).toBe("new-password");
    expect((screen.getByLabelText("Credential") as HTMLInputElement).readOnly).toBe(true);
  });

  it("posts the secret to the store only; the chat message carries no secret", async () => {
    const onAnswer = show();
    const secret = screen.getByLabelText("Secret") as HTMLInputElement;
    fireEvent.change(secret, { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith("Credential lab-sonic stored.", "s_abc"));
    expect(consoleApi.saveCredential).toHaveBeenCalledWith(
      expect.objectContaining({ name: "lab-sonic", kind: "password", secret: SECRET }),
    );
    expect(JSON.stringify(onAnswer.mock.calls)).not.toContain(SECRET);
    expect(secret.value).toBe("");
    expect(document.body.textContent).not.toContain(SECRET);
    expect(document.body.textContent).toContain("ab12cd");
  });

  it("shows the server's refusal and keeps the secret out of the page", async () => {
    vi.mocked(consoleApi.saveCredential).mockRejectedValue(new Error("no key configured"));
    const onAnswer = show();
    fireEvent.change(screen.getByLabelText("Secret"), { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("no key configured");
    expect(onAnswer).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("without plugins.manage shows a note and no field", () => {
    permissions = [];
    show();
    expect(screen.queryByLabelText("Secret")).toBeNull();
    expect(screen.getByText(/ask an administrator/i)).toBeTruthy();
  });
});
