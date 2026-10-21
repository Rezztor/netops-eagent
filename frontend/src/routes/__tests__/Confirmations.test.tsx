// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/session", () => ({
  useSession: () => ({ user: { id: "me" }, preferences: { timezone: "UTC" }, permissions: [] }),
  useCan: () => true,
}));

vi.mock("@/api/admin", () => ({
  listUsers: vi.fn(async () => ({
    ok: true,
    users: [
      { id: "me", username: "admin", display_name: "Admin", email: "", role: "admin", disabled: false, created_at: "", created_by: "", last_login: null, must_change_password: false },
      { id: "b", username: "bob", display_name: "Bob", email: "", role: "user", disabled: false, created_at: "", created_by: "", last_login: null, must_change_password: false },
    ],
    roles: [
      { id: "admin", title: "Administrator", description: "", permissions: [] },
      { id: "power", title: "Power User", description: "", permissions: [] },
      { id: "user", title: "Standard User", description: "", permissions: [] },
    ],
  })),
  updateUser: vi.fn(async () => ({ ok: true })),
  createUser: vi.fn(),
  deleteUser: vi.fn(),
  changePassword: vi.fn(),
  listPlugins: vi.fn(async () => ({ ok: true, plugins: [{ name: "_memory", path: "", display_name: "Memory", toggle_state: "enabled" }] })),
  togglePlugin: vi.fn(async () => ({ ok: true })),
  getPluginDoc: vi.fn(),
}));

import { listUsers, togglePlugin, updateUser } from "@/api/admin";
import { ConfirmProvider, ToastProvider } from "@/components";
import { PluginsScreen } from "../PluginsScreen";
import { UsersScreen } from "../UsersScreen";

afterEach(() => {
  cleanup();
  vi.mocked(updateUser).mockClear();
  vi.mocked(togglePlugin).mockClear();
});

function show(node: React.ReactNode) {
  render(
    <MemoryRouter>
      <ToastProvider>
        <ConfirmProvider>{node}</ConfirmProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("disabling a user", () => {
  it("asks first, and Cancel leaves the account alone", async () => {
    show(<UsersScreen />);
    await waitFor(() => expect(listUsers).toHaveBeenCalled());
    const bob = (await screen.findAllByText("bob"))[0]!.closest("tr")!;
    fireEvent.click(within(bob).getByRole("button", { name: "Disable" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Disable bob?");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(updateUser).not.toHaveBeenCalled();
  });
});

describe("changing a role", () => {
  it("names the role, not its id", async () => {
    show(<UsersScreen />);
    const bob = (await screen.findAllByText("bob"))[0]!.closest("tr")!;
    fireEvent.change(within(bob).getByRole("combobox"), { target: { value: "power" } });
    expect((await screen.findByRole("dialog")).textContent).toContain("Make bob a Power User?");
  });
});

describe("toggling a plugin", () => {
  it("asks first, because it changes every user's agent", async () => {
    show(<PluginsScreen />);
    fireEvent.click(await screen.findByRole("switch"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Disable Memory?");
    fireEvent.click(within(dialog).getByRole("button", { name: "Disable" }));
    await waitFor(() => expect(togglePlugin).toHaveBeenCalledWith("_memory", false));
  });
});
