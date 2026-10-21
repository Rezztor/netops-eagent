// @vitest-environment jsdom
/**
 * A card answer pressed while the composer is sending must not be dropped.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ConfirmProvider, ToastProvider } from "@/components";
import type { PollResponse } from "@/api/chat";

vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC" }, user: { id: "u1" } }),
  useCan: () => false,
}));

vi.mock("@/lib/realtime", () => ({
  useRealtime: () => ({
    state: null,
    connection: "offline",
    contextId: "ctx-1",
    setContext: vi.fn(),
    realtime: null,
  }),
}));

vi.mock("@/lib/dictation", () => ({
  useDictation: () => ({ supported: false, listening: false, toggle: () => {} }),
}));

vi.mock("../ConversationList", () => ({ ConversationList: () => null }));
vi.mock("../ConversationPanel", () => ({ ConversationPanel: () => null }));
vi.mock("../ThreadToolbar", () => ({ ThreadToolbar: () => null }));
vi.mock("../FindInConversation", () => ({ FindInConversation: () => null }));

vi.mock("../Transcript", () => ({
  Transcript: ({
    onAnswer,
  }: {
    onAnswer: (text: string, answers: string) => void;
  }) => (
    <button type="button" onClick={() => onAnswer("Approved on the card.", "q1")}>
      Answer card
    </button>
  ),
}));

let releaseSend: (() => void) | null = null;

vi.mock("@/api/chat", () => ({
  poll: vi.fn(async () => ({
    context: "ctx-1",
    contexts: [{ id: "ctx-1", name: "A" }],
    // Non-empty so the transcript (and its answer path) is mounted.
    logs: [{ no: 0, type: "agent", content: "Pick one?" }],
    log_guid: "g",
    log_version: 1,
    log_progress: "",
    log_progress_active: false,
    paused: false,
  } satisfies PollResponse)),
  listCommands: vi.fn(async () => []),
  sendMessage: vi.fn(
    () =>
      new Promise<{ message: string; context: string }>((resolve) => {
        releaseSend = () => resolve({ message: "", context: "ctx-1" });
      }),
  ),
  sendMessageWithAttachments: vi.fn(),
  queueAdd: vi.fn(),
  getContextWindow: vi.fn(),
  createChat: vi.fn(),
  removeChat: vi.fn(),
  resetChat: vi.fn(),
  branchChat: vi.fn(),
  compactChat: vi.fn(),
  exportChat: vi.fn(),
  getCompactionStats: vi.fn(),
  nudge: vi.fn(),
  stop: vi.fn(),
  setPaused: vi.fn(),
  queueRemove: vi.fn(),
  queueSend: vi.fn(),
}));

import { ChatScreen } from "../ChatScreen";
import { sendMessage } from "@/api/chat";

afterEach(() => {
  cleanup();
  releaseSend = null;
  vi.mocked(sendMessage).mockClear();
});

beforeEach(() => {
  vi.mocked(sendMessage).mockImplementation(
    () =>
      new Promise<{ message: string; context: string }>((resolve) => {
        releaseSend = () => resolve({ message: "", context: "ctx-1" });
      }),
  );
});

describe("ChatScreen card answers while sending", () => {
  it("sends a card answer after the in-flight message finishes instead of dropping it", async () => {
    render(
      <MemoryRouter>
        <ToastProvider>
          <ConfirmProvider>
            <ChatScreen />
          </ConfirmProvider>
        </ToastProvider>
      </MemoryRouter>,
    );

    const message = await screen.findByLabelText("Message");
    fireEvent.change(message, { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    expect(vi.mocked(sendMessage).mock.calls[0]?.[0]).toBe("hello");

    // Card answer while the first send is still in flight.
    fireEvent.click(screen.getByRole("button", { name: "Answer card" }));

    // Without a queue this is never sent; with one it waits for the first to finish.
    expect(sendMessage).toHaveBeenCalledTimes(1);

    releaseSend?.();
    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(2));
    expect(vi.mocked(sendMessage).mock.calls[1]?.[0]).toBe("Approved on the card.");
    expect(vi.mocked(sendMessage).mock.calls[1]?.[2]).toBe("q1");
  });
});
