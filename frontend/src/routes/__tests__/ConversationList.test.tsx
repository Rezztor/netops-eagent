// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as chatApi from "@/api/chat";
import type { ChatContext } from "@/api/chat";
import { ToastProvider } from "@/components";
import { ConversationList } from "../ConversationList";

const removeChats = vi.fn();

vi.mock("@/api/chat", async (importOriginal) => ({
  ...(await importOriginal<typeof chatApi>()),
  searchChats: vi.fn().mockResolvedValue({ chats: [] }),
  removeChats: (...args: unknown[]) => removeChats(...args),
}));

const memoryStore = new Map<string, string>();
const memoryStorage = {
  getItem: (key: string) => memoryStore.get(key) ?? null,
  setItem: (key: string, value: string) => {
    memoryStore.set(key, String(value));
  },
  removeItem: (key: string) => {
    memoryStore.delete(key);
  },
  clear: () => memoryStore.clear(),
  key: (index: number) => [...memoryStore.keys()][index] ?? null,
  get length() {
    return memoryStore.size;
  },
};
Object.defineProperty(globalThis, "localStorage", { value: memoryStorage, configurable: true });

afterEach(() => {
  cleanup();
  memoryStore.clear();
  removeChats.mockReset();
});

beforeEach(() => {
  memoryStore.clear();
});

const now = new Date().toISOString();
const parent: ChatContext = {
  id: "parentid1234",
  name: "Main chat",
  last_message: now,
  agent_profile_label: "Primary",
};
const kids: ChatContext[] = [
  {
    id: "childid11111",
    name: "Developer: Research",
    last_message: now,
    agent_profile_label: "Developer",
    parent_context_kind: "subordinate",
    parent_context_id: parent.id,
  },
  {
    id: "childid22222",
    name: "Developer: Review",
    last_message: now,
    parent_context_kind: "subordinate",
    parent_context_id: parent.id,
    running: true,
  },
];
const other: ChatContext = {
  id: "otherid99999",
  name: "Other chat",
  last_message: now,
  agent_profile_label: "Primary",
};

function renderList(
  props: Partial<{
    activeId: string;
    selectMode: boolean;
    onSelectModeChange: (next: boolean) => void;
    onPick: (id: string) => void;
    onRemoved: (removed: string[], remaining: ChatContext[]) => void;
    contexts: ChatContext[];
  }> = {},
) {
  const onSelectModeChange = props.onSelectModeChange ?? vi.fn();
  const onPick = props.onPick ?? vi.fn();
  const onRemoved = props.onRemoved ?? vi.fn();
  return {
    onPick,
    onSelectModeChange,
    onRemoved,
    ...render(
      <ToastProvider>
        <ConversationList
          contexts={props.contexts ?? [parent, ...kids, other]}
          activeId={props.activeId ?? ""}
          onPick={onPick}
          relativeTime={() => "5m ago"}
          selectMode={props.selectMode ?? false}
          onSelectModeChange={onSelectModeChange}
          onRemoved={onRemoved}
        />
      </ToastProvider>,
    ),
  };
}

describe("ConversationList fold", () => {
  it("shows the profile label and time, never the raw id", () => {
    const { container } = renderList();
    const row = screen.getByTitle("Main chat");
    expect(row.textContent).toContain("Primary");
    expect(row.textContent).toContain("5m ago");
    expect(container.textContent).not.toContain("parentid");
    expect(screen.getByText("Today")).toBeTruthy();
  });

  it("collapses sub-agents by default and expands on the chevron chip", () => {
    renderList();
    const toggle = screen.getByRole("button", { name: "2 sub-agents" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTitle("Developer: Research")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTitle("Developer: Research")).toBeTruthy();
    expect(screen.getByTitle("Developer: Review")).toBeTruthy();
  });

  it("persists expand state in localStorage", () => {
    const { unmount } = renderList();
    fireEvent.click(screen.getByRole("button", { name: "2 sub-agents" }));
    expect(JSON.parse(localStorage.getItem("eagent.chat.expanded")!)).toContain(parent.id);
    unmount();
    renderList();
    expect(screen.getByRole("button", { name: "2 sub-agents" }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect(screen.getByTitle("Developer: Research")).toBeTruthy();
  });

  it("auto-expands when the active conversation is a child", () => {
    renderList({ activeId: "childid22222" });
    const toggle = screen.getByRole("button", { name: "2 sub-agents" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTitle("Developer: Review")).toBeTruthy();
  });

  it("ArrowRight expands, ArrowLeft collapses, Enter opens", () => {
    const onPick = vi.fn();
    renderList({ onPick });
    const row = screen.getByTitle("Main chat");
    fireEvent.keyDown(row, { key: "ArrowRight" });
    expect(screen.getByRole("button", { name: "2 sub-agents" }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    fireEvent.keyDown(row, { key: "ArrowLeft" });
    // Still auto-visible only when active is a child; here active is "", so collapses.
    expect(screen.getByRole("button", { name: "2 sub-agents" }).getAttribute("aria-expanded")).toBe(
      "false",
    );
    fireEvent.keyDown(row, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith(parent.id);
  });
});

describe("ConversationList select mode", () => {
  it("enters select mode via controlled prop and exits on Cancel / Esc", () => {
    const onSelectModeChange = vi.fn();
    const { rerender } = render(
      <ToastProvider>
        <ConversationList
          contexts={[parent, ...kids, other]}
          activeId=""
          onPick={vi.fn()}
          relativeTime={() => "5m ago"}
          selectMode
          onSelectModeChange={onSelectModeChange}
        />
      </ToastProvider>,
    );
    expect(screen.getByText("0 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onSelectModeChange).toHaveBeenCalledWith(false);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onSelectModeChange).toHaveBeenCalledWith(false);

    rerender(
      <ToastProvider>
        <ConversationList
          contexts={[parent, ...kids, other]}
          activeId=""
          onPick={vi.fn()}
          relativeTime={() => "5m ago"}
          selectMode={false}
          onSelectModeChange={onSelectModeChange}
        />
      </ToastProvider>,
    );
    expect(screen.queryByText(/selected/)).toBeNull();
  });

  it("Ctrl-click enters select mode with that row", () => {
    const onSelectModeChange = vi.fn();
    renderList({ onSelectModeChange });
    fireEvent.click(screen.getByTitle("Other chat"), { ctrlKey: true });
    expect(onSelectModeChange).toHaveBeenCalledWith(true);
  });

  it("Shift-click selects a visible range", () => {
    // Expand so children are in visible order.
    localStorage.setItem("eagent.chat.expanded", JSON.stringify([parent.id]));
    renderList({ selectMode: true });
    fireEvent.click(screen.getByTitle("Main chat"));
    fireEvent.click(screen.getByTitle("Other chat"), { shiftKey: true });
    // Parent + its kids (toggled with parent) + Other, or range of visible rows.
    expect(screen.getByText(/selected/)).toBeTruthy();
    const count = Number(screen.getByText(/^\d+ selected$/).textContent!.split(" ")[0]);
    expect(count).toBeGreaterThanOrEqual(2);
  });

  it("parent checkbox ticks children; partial kids are indeterminate", () => {
    localStorage.setItem("eagent.chat.expanded", JSON.stringify([parent.id]));
    renderList({ selectMode: true });
    expect(screen.getByRole("button", { name: "2 sub-agents" }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    const parentBox = screen.getByRole("checkbox", { name: "Main chat" }) as HTMLInputElement;
    fireEvent.click(parentBox);
    expect(parentBox.checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Developer: Research" }) as HTMLInputElement).checked).toBe(
      true,
    );

    // Untick one child → parent indeterminate
    fireEvent.click(screen.getByRole("checkbox", { name: "Developer: Review" }));
    expect(parentBox.indeterminate).toBe(true);
  });

  it("Select all with a search only selects matches", async () => {
    renderList({ selectMode: true });
    fireEvent.change(screen.getByLabelText("Search conversations"), {
      target: { value: "Other" },
    });
    await waitFor(() => expect(screen.getByTitle("Other chat")).toBeTruthy());
    expect(screen.queryByTitle("Main chat")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  it("delete dialog shows titles, cascade checkbox default on, running warning; request body respects include_subagents off", async () => {
    removeChats.mockResolvedValue({
      removed: [parent.id, kids[0]!.id, kids[1]!.id],
      failed: [],
      subagents_removed: 0,
    });
    localStorage.setItem("eagent.chat.expanded", JSON.stringify([parent.id]));
    renderList({ selectMode: true });
    fireEvent.click(screen.getByRole("checkbox", { name: "Main chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Delete 3 conversations/)).toBeTruthy();
    expect(within(dialog).getByText("Main chat")).toBeTruthy();
    const cascade = within(dialog).getByRole("checkbox", {
      name: /Also delete their sub-agent chats \(2\)/,
    }) as HTMLInputElement;
    expect(cascade.checked).toBe(true);
    expect(within(dialog).getByText(/still running and will be stopped/)).toBeTruthy();
    expect(within(dialog).getByText("This cannot be undone.")).toBeTruthy();

    fireEvent.click(cascade); // off
    fireEvent.click(within(dialog).getByRole("button", { name: /Delete \d+ chats?/ }));
    await waitFor(() =>
      expect(removeChats).toHaveBeenCalledWith(
        expect.arrayContaining([parent.id, kids[0]!.id, kids[1]!.id]),
        false,
      ),
    );
  });

  it("shows a partial-failure toast", async () => {
    removeChats.mockResolvedValue({
      removed: [other.id],
      failed: [{ id: parent.id, error: "not yours" }],
      subagents_removed: 0,
    });
    renderList({ selectMode: true });
    fireEvent.click(screen.getByRole("checkbox", { name: "Other chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Delete \d+ chats?/ }));
    await waitFor(() =>
      expect(screen.getByText(/1 deleted, 1 could not be removed: not yours/)).toBeTruthy(),
    );
  });

  it("hands off the active chat via onRemoved", async () => {
    removeChats.mockResolvedValue({
      removed: [other.id],
      failed: [],
      subagents_removed: 0,
    });
    const onRemoved = vi.fn();
    renderList({ selectMode: true, activeId: other.id, onRemoved });
    fireEvent.click(screen.getByRole("checkbox", { name: "Other chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Delete \d+ chats?/ }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalled());
    const [removed, remaining] = onRemoved.mock.calls[0]!;
    expect(removed).toEqual([other.id]);
    expect(remaining.map((c: ChatContext) => c.id)).not.toContain(other.id);
  });

  it("does not open the chat when clicking a row in select mode", () => {
    const onPick = vi.fn();
    renderList({ selectMode: true, onPick });
    fireEvent.click(screen.getByTitle("Other chat"));
    expect(onPick).not.toHaveBeenCalled();
    expect(screen.getByText("1 selected")).toBeTruthy();
  });
});
