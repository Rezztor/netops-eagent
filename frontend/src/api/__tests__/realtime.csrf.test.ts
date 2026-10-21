/**
 * After a server restart the CSRF cookie name changes with the runtime id.
 * The socket can still connect while every handler is refused, which used to
 * leave the UI saying "live" with no data and no recovery.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const handlers: Record<string, (...args: unknown[]) => void> = {};
const mockSocket = {
  connected: false,
  connect: vi.fn(() => {
    mockSocket.connected = true;
    handlers.connect?.();
  }),
  disconnect: vi.fn(() => {
    mockSocket.connected = false;
    handlers.disconnect?.();
  }),
  on: vi.fn((event: string, fn: (...args: unknown[]) => void) => {
    handlers[event] = fn;
  }),
  off: vi.fn(),
  emit: vi.fn(),
};

const refreshCsrfToken = vi.fn(async () => "fresh-token");
const getCsrfToken = vi.fn(() => "stale-token");

vi.mock("socket.io-client", () => ({
  io: () => mockSocket,
}));

vi.mock("../client", () => ({
  getCsrfToken: () => getCsrfToken(),
  refreshCsrfToken: () => refreshCsrfToken(),
}));

import { isSocketHandlerRefusal, RealtimeState } from "../realtime";

const refusal = {
  correlationId: "c1",
  results: [
    {
      handlerId: "ws.dispatch",
      ok: false,
      error: { code: "NO_HANDLERS", error: "No handlers activated" },
    },
  ],
};

describe("isSocketHandlerRefusal", () => {
  it("recognises a NO_HANDLERS ack from a connect that activated nothing", () => {
    expect(isSocketHandlerRefusal(refusal)).toBe(true);
    expect(isSocketHandlerRefusal({ results: [{ ok: true }] })).toBe(false);
  });
});

describe("RealtimeState CSRF recovery", () => {
  beforeEach(() => {
    for (const key of Object.keys(handlers)) delete handlers[key];
    mockSocket.connected = false;
    mockSocket.connect.mockClear();
    mockSocket.disconnect.mockClear();
    mockSocket.emit.mockClear();
    mockSocket.on.mockClear();
    refreshCsrfToken.mockClear();
    getCsrfToken.mockClear();
    refreshCsrfToken.mockResolvedValue("fresh-token");
  });

  it("fetches a fresh CSRF token and reconnects when handlers are refused", async () => {
    mockSocket.emit.mockImplementation(
      (_event: string, _payload: unknown, ack?: (reply: unknown) => void) => {
        ack?.(refusal);
      },
    );

    const connectionChanges: string[] = [];
    const rt = new RealtimeState();
    rt.connect(
      { context: "ctx-1", timezone: "UTC" },
      {
        onState: () => {},
        onConnectionChange: (state) => connectionChanges.push(state),
      },
    );

    // Connection establishes (looks live) even though handlers were refused.
    mockSocket.connected = true;
    handlers.connect?.();

    await vi.waitFor(() => expect(refreshCsrfToken).toHaveBeenCalledTimes(1));
    expect(mockSocket.disconnect).toHaveBeenCalled();
    expect(mockSocket.connect).toHaveBeenCalled();
    // Must not keep advertising live while handlers are dead.
    expect(connectionChanges).toContain("offline");
  });
});
