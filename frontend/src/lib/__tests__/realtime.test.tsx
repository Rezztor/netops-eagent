// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const connect = vi.fn();
const setContext = vi.fn();

vi.mock("@/lib/session", () => ({
  useSession: () => ({ preferences: { timezone: "UTC" } }),
}));
vi.mock("@/api/realtime", () => ({
  RealtimeState: class {
    connect = connect;
    setContext = setContext;
    disconnect = vi.fn();
  },
}));

import { RealtimeProvider, useRealtime } from "../realtime";

/** What ChatScreen does on a reload: name the conversation from the URL in its first effect. */
function NamesConversationOnMount() {
  const { setContext: choose } = useRealtime();
  useEffect(() => choose("abc123"), [choose]);
  return null;
}

describe("RealtimeProvider", () => {
  beforeEach(() => {
    connect.mockClear();
    setContext.mockClear();
  });

  it("opens the connection on a conversation named before the connection existed", () => {
    // A child's effect runs before its parent's, so the screen names the
    // conversation while the provider still has no connection to tell.
    render(
      <RealtimeProvider>
        <NamesConversationOnMount />
      </RealtimeProvider>,
    );

    expect(connect).toHaveBeenCalledTimes(1);
    expect(connect.mock.calls[0]?.[0]).toMatchObject({ context: "abc123" });
  });
});
