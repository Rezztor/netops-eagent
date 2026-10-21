// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PERMISSIONS } from "@/api/types";
import { SIDEBAR_PINNED_KEY } from "@/lib/useSidebar";
import { AppShell } from "../AppShell";

vi.mock("@/lib/session", () => ({
  useSession: () => session,
}));

vi.mock("@/lib/realtime", () => ({
  RealtimeProvider: ({ children }: { children: ReactNode }) => children,
  useRealtime: () => ({
    state: null,
    connection: "connected",
    setContext: vi.fn(),
    contextId: "",
    realtime: null,
  }),
}));

vi.mock("@/api/inventorySummary", () => ({
  getInventorySummary: vi.fn().mockResolvedValue({ ok: true, pending: 3, conflicts: 2 }),
}));
vi.mock("../OverviewScreen", () => ({ OverviewScreen: () => <div>Overview body</div> }));
vi.mock("../ChatScreen", () => ({ ChatScreen: () => <div>Chat body</div> }));
vi.mock("../FilesScreen", () => ({ FilesScreen: () => <div>Files body</div> }));
vi.mock("../SkillsScreen", () => ({ SkillsScreen: () => <div>Skills body</div> }));
vi.mock("../SchedulesScreen", () => ({ SchedulesScreen: () => <div>Schedules body</div> }));
vi.mock("../UsersScreen", () => ({ UsersScreen: () => <div>Users body</div> }));
vi.mock("../PluginsScreen", () => ({ PluginsScreen: () => <div>Plugins body</div> }));
vi.mock("../ModelsScreen", () => ({ ModelsScreen: () => <div>Models body</div> }));
vi.mock("../settings/SettingsScreen", () => ({
  SettingsScreen: () => <div>Settings body</div>,
}));
vi.mock("../ActivityScreen", () => ({ ActivityScreen: () => <div>Activity body</div> }));
vi.mock("../NetworkScreen", () => ({ NetworkScreen: () => <div>Network body</div> }));
vi.mock("../InventoryScreen", () => ({ InventoryScreen: () => <div>Inventory body</div> }));
vi.mock("../OperationsScreen", () => ({ OperationsScreen: () => <div>Operations body</div> }));
vi.mock("../Notifications", () => ({
  Notifications: () => null,
}));
vi.mock("../ThemeToggle", () => ({
  ThemeToggle: () => null,
}));

const ALL_PERMS = Object.values(PERMISSIONS);

function installMemoryStorage() {
  const store = new Map<string, string>();
  const memory: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => {
      store.set(key, String(value));
    },
    removeItem: (key) => {
      store.delete(key);
    },
    key: (index) => [...store.keys()][index] ?? null,
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: memory,
    configurable: true,
    writable: true,
  });
}

let session: {
  authenticated: true;
  user: {
    id: string;
    username: string;
    display_name: string;
    email: string;
    role: "admin";
    role_title: string;
    must_change_password: boolean;
  };
  permissions: string[];
  preferences: { timezone: string; time_format: string; ui_control_visibility: Record<string, unknown> };
  csrfToken: string;
  runtime: { runtime_id: string; is_development: boolean; is_dockerized: boolean };
  version: { version: string; commit_time: string };
  webuiExtensions: null;
  licence: object | null;
  update: { phase: string; version: string } | null;
  updatesAvailable?: number | null;
} = {
  authenticated: true,
  user: {
    id: "u1",
    username: "admin",
    display_name: "Admin",
    email: "a@example.com",
    role: "admin",
    role_title: "Administrator",
    must_change_password: false,
  },
  permissions: ALL_PERMS as string[],
  preferences: { timezone: "UTC", time_format: "24h", ui_control_visibility: {} },
  csrfToken: "csrf",
  runtime: { runtime_id: "r", is_development: false, is_dockerized: true },
  version: { version: "1.0.0", commit_time: "" },
  webuiExtensions: null,
  licence: null,
  update: null,
};

function renderShell(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/*" element={<AppShell />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  installMemoryStorage();
  session = {
    ...session,
    permissions: ALL_PERMS as string[],
  };
});

afterEach(() => {
  cleanup();
});

describe("AppShell landmarks", () => {
  it("gives every page a skip link, a main region, one h1 and its own document title", async () => {
    renderShell("/files");
    await screen.findByText("Files body");
    expect(screen.getByRole("link", { name: "Skip to content" }).getAttribute("href")).toBe("#content");
    expect(screen.getByRole("main").id).toBe("content");
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Files"]);
    expect(document.title).toBe("Files · Rezztor EAgent");
  });
});

describe("AppShell on a narrow screen", () => {
  afterEach(() => {
    // @ts-expect-error -- removing the stub restores jsdom's own (absent) matchMedia
    delete window.matchMedia;
  });

  it("puts the rail in a drawer behind the menu button, closed by Escape and by navigating", () => {
    window.matchMedia = ((query: string) => ({
      matches: true,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    renderShell();
    const shell = document.querySelector("[data-sidebar]")!;
    expect(shell.getAttribute("data-sidebar")).toBe("drawer");
    expect(shell.getAttribute("data-menu")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(shell.getAttribute("data-menu")).toBe("open");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(shell.getAttribute("data-menu")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    fireEvent.click(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Files" }));
    expect(shell.getAttribute("data-menu")).toBeNull();
  });
});

describe("AppShell wording", () => {
  it("shows the role by name, the version without its branch letter, and no unmeasured health claim", () => {
    session = { ...session, version: { version: "U v2026.10.17+14", commit_time: "" } };
    renderShell();
    expect(screen.getByText("v2026.10.17+14")).toBeTruthy();
    expect(screen.queryByText(/U v2026/)).toBeNull();
    expect(screen.queryByText(/permissions/)).toBeNull();
    expect(screen.getAllByText("Administrator").length).toBeGreaterThan(0);
    expect(screen.queryByText("All systems operational")).toBeNull();
    session = { ...session, version: { version: "1.0.0", commit_time: "" } };
  });
});

describe("AppShell navigation", () => {
  it("puts every administrator-only screen in one badged Administration group", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const label of ["Users", "Plugins", "Models", "System", "Activity"]) {
      expect(within(within(nav).getByRole("link", { name: label })).getByText("admin")).toBeTruthy();
    }
    for (const label of ["Chat", "Settings", "My schedules"]) {
      expect(within(within(nav).getByRole("link", { name: label })).queryByText("admin")).toBeNull();
    }
  });

  it("sends the old Operations address to System", async () => {
    renderShell("/operations");
    expect(await screen.findByText("Operations body")).toBeTruthy();
    expect(document.title).toBe("System · Rezztor EAgent");
  });
});

describe("AppShell sidebar", () => {
  it("shows proposals plus conflicts as a count on the Inventory item", async () => {
    renderShell();
    expect((await screen.findByTestId("inventory-review-count")).textContent).toBe("5");
  });

  it("defaults to pinned with aria-pressed pin button", () => {
    renderShell();
    const shell = document.querySelector("[data-sidebar]");
    expect(shell?.getAttribute("data-sidebar")).toBe("pinned");
    const pin = screen.getByRole("button", { name: "Unpin sidebar" });
    expect(pin.getAttribute("aria-pressed")).toBe("true");
  });

  it("unpins to rail and shows accessible names for every nav item", () => {
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Unpin sidebar" }));
    expect(document.querySelector("[data-sidebar]")?.getAttribute("data-sidebar")).toBe(
      "rail",
    );
    expect(localStorage.getItem(SIDEBAR_PINNED_KEY)).toBe("0");

    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const label of [
      "Overview",
      "Chat",
      "Files",
      "Skills",
      "My schedules",
      "Network",
      "Inventory",
      "Users",
      "Plugins",
      "Models",
      "Settings",
      "System",
      "Activity",
    ]) {
      expect(within(nav).getByRole("link", { name: label })).toBeTruthy();
    }

    const pin = screen.getByRole("button", { name: "Pin sidebar", hidden: true });
    expect(pin.getAttribute("aria-pressed")).toBe("false");
    expect(
      screen.getByRole("button", { name: "Show full sidebar" }).getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("opens peek via chevron and closes on Escape", () => {
    localStorage.setItem(SIDEBAR_PINNED_KEY, "0");
    renderShell();
    const chevron = screen.getByRole("button", { name: "Show full sidebar" });
    fireEvent.click(chevron);
    expect(document.querySelector("[data-peeking]")).toBeTruthy();
    expect(chevron.getAttribute("aria-expanded")).toBe("true");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.querySelector("[data-peeking]")).toBeNull();
  });

  it("opens peek on focus within the rail", () => {
    localStorage.setItem(SIDEBAR_PINNED_KEY, "0");
    renderShell();
    const overview = screen.getByRole("link", { name: "Overview" });
    fireEvent.focus(overview);
    expect(document.querySelector("[data-peeking]")).toBeTruthy();
  });

  it("closes peek on outside click", () => {
    localStorage.setItem(SIDEBAR_PINNED_KEY, "0");
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Show full sidebar" }));
    expect(document.querySelector("[data-peeking]")).toBeTruthy();
    fireEvent.pointerDown(screen.getByText("Overview body"));
    expect(document.querySelector("[data-peeking]")).toBeNull();
  });

  it("closes peek on route change", () => {
    localStorage.setItem(SIDEBAR_PINNED_KEY, "0");
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Show full sidebar" }));
    expect(document.querySelector("[data-peeking]")).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: "Chat" }));
    expect(document.querySelector("[data-peeking]")).toBeNull();
    expect(screen.getByText("Chat body")).toBeTruthy();
  });

  it("pins from the peek pin button", () => {
    localStorage.setItem(SIDEBAR_PINNED_KEY, "0");
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Show full sidebar" }));
    fireEvent.click(screen.getByRole("button", { name: "Pin sidebar" }));
    expect(document.querySelector("[data-sidebar]")?.getAttribute("data-sidebar")).toBe(
      "pinned",
    );
    expect(document.querySelector("[data-peeking]")).toBeNull();
    expect(localStorage.getItem(SIDEBAR_PINNED_KEY)).toBe("1");
  });
});

describe("AppShell licence banner", () => {
  const withLicence = (licence: object, permissions: string[] = ALL_PERMS) => {
    session = { ...session, permissions, licence: licence as never };
    renderShell();
  };

  afterEach(() => {
    session = { ...session, licence: null };
  });

  it("tells everybody what a restricted installation still does, and where to fix it", () => {
    withLicence(
      { state: "restricted", agent_runs_allowed: false, grace_days_remaining: null, reason: "No licence is installed." },
      [PERMISSIONS.chatUse],
    );
    const banner = screen.getByText(/This installation is restricted\. No licence is installed\./);
    expect(banner.textContent).toContain("sign-in, reading, export and settings keep working");
    expect(banner.textContent).toContain("licence page");
  });

  it("shows the first grace period to administrators only", () => {
    const grace1 = { state: "grace1", agent_runs_allowed: true, grace_days_remaining: 20, reason: "x" };
    withLicence(grace1, [PERMISSIONS.chatUse]);
    expect(screen.queryByText(/could not be renewed/)).toBeNull();
    cleanup();
    withLicence(grace1);
    expect(screen.getByText(/Everything keeps working for 20\s+days more/)).toBeTruthy();
  });

  it("shows the final grace period to everybody", () => {
    withLicence(
      { state: "grace2", agent_runs_allowed: true, grace_days_remaining: 1, reason: "x" },
      [PERMISSIONS.chatUse],
    );
    expect(screen.getByText(/Everything keeps working for 1\s+day more/)).toBeTruthy();
  });

  it("is quiet when licensed", () => {
    withLicence({ state: "licensed", agent_runs_allowed: true, grace_days_remaining: null, reason: null });
    expect(screen.queryByText(/licence/i)).toBeNull();
  });
});

describe("AppShell update banner", () => {
  afterEach(() => {
    session = { ...session, update: null, updatesAvailable: null, permissions: ALL_PERMS };
  });

  it("tells administrators when a staged update waits for a restart", () => {
    session = {
      ...session,
      update: { phase: "staged", version: "2026.10.12" },
      permissions: ALL_PERMS,
    };
    renderShell();
    const banner = screen.getByText(/Update 2026.10.12 is installed and waits for a restart\./);
    expect(banner.querySelector("a")?.getAttribute("href")).toBe("/settings/updates");
  });

  it("tells administrators how many updates Rezztor's update service lists", () => {
    session = { ...session, update: null, updatesAvailable: 3, permissions: ALL_PERMS };
    renderShell();
    const banner = screen.getByText(/3 updates available\./);
    expect(banner.querySelector("a")?.getAttribute("href")).toBe("/settings/updates");
  });

  it("says a restart that waits rather than the count, and nothing to other accounts", () => {
    session = { ...session, update: { phase: "staged", version: "2026.10.12" }, updatesAvailable: 3, permissions: ALL_PERMS };
    renderShell();
    expect(screen.queryByText(/updates available/)).toBeNull();
    cleanup();
    session = { ...session, update: null, updatesAvailable: 3, permissions: [PERMISSIONS.chatUse] };
    renderShell();
    expect(screen.queryByText(/updates available/)).toBeNull();
  });

  it("hides the staged update from accounts without settings.manage", () => {
    session = {
      ...session,
      update: { phase: "staged", version: "2026.10.12" },
      permissions: [PERMISSIONS.chatUse],
    };
    renderShell();
    expect(screen.queryByText(/waits for a restart/)).toBeNull();
  });
});
