// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChangePasswordScreen } from "../ChangePasswordScreen";

const reload = vi.fn();
vi.mock("@/lib/session", () => ({
  useSession: () => ({ user: { display_name: "Test User" } }),
  useReloadSession: () => reload,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  reload.mockReset();
});

describe("forced password change", () => {
  it("shows the server's refusal instead of reloading as if it had worked", async () => {
    // change_password answers a wrong current password with HTTP 200 {ok: false}.
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ ok: false, error: "Current password is incorrect." }), { status: 200 }),
    ));
    render(<ChangePasswordScreen />);
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "wrong-one" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "long-enough-1" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "long-enough-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));

    await waitFor(() => expect(screen.getByText("Current password is incorrect.")).toBeTruthy());
    expect(reload).not.toHaveBeenCalled();
  });

  it("states the rule before it is broken, carries the brand and offers a way out", () => {
    render(<ChangePasswordScreen />);
    expect(screen.getByText("At least 12 characters.")).toBeTruthy();
    expect(screen.getByText("Rezztor EAgent")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("refuse your requests");
  });
});
