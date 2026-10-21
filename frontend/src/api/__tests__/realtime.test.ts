/**
 * Tests for the incremental-push merge.
 *
 * This is the part that fails silently. The server advances `log_from` after
 * each push, so every push after the first carries only what changed. A client
 * that replaces its log looks fine on the first render and then shows a
 * conversation with its history missing — which reads as a backend problem.
 */

import { describe, expect, it } from "vitest";

import { StateAccumulator, forContext, mergeLogs, pickChatState, unwrapSnapshot } from "../realtime";
import type { LogItem, PollResponse } from "../chat";

const entry = (no: number, content: string, type = "response"): LogItem => ({
  no,
  type,
  content,
});

function snapshot(over: Partial<PollResponse> = {}): Partial<PollResponse> {
  return { context: "ctx-1", contexts: [], logs: [], log_version: 0, ...over };
}

describe("mergeLogs", () => {
  it("appends entries that are new", () => {
    const merged = mergeLogs([entry(0, "first")], [entry(1, "second")]);
    expect(merged.map((e) => e.content)).toEqual(["first", "second"]);
  });

  it("replaces an entry revised in place rather than duplicating it", () => {
    // The backend grows a message by revising the same entry number as output
    // streams in. Appending would show the message once per batch.
    const merged = mergeLogs([entry(3, "Hel")], [entry(3, "Hello")]);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.content).toBe("Hello");
  });

  it("keeps entries ordered by number regardless of arrival order", () => {
    const merged = mergeLogs([entry(5, "e")], [entry(1, "a"), entry(3, "c")]);
    expect(merged.map((e) => e.no)).toEqual([1, 3, 5]);
  });
});

describe("StateAccumulator", () => {
  it("accumulates across incremental pushes instead of replacing", () => {
    const acc = new StateAccumulator();
    acc.reset("ctx-1");

    acc.apply(snapshot({ logs: [entry(0, "you: hi")], log_version: 1 }));
    // The second push carries only what is new -- this is what the server sends.
    const state = acc.apply(snapshot({ logs: [entry(1, "agent: hello")], log_version: 2 }));

    expect(state.logs).toHaveLength(2);
    expect(state.logs.map((e) => e.content)).toEqual(["you: hi", "agent: hello"]);
  });

  it("ignores a push for a conversation that is no longer displayed", () => {
    // Switching chats leaves an in-flight push for the previous one. Merging it
    // would splice another conversation's messages into this one.
    const acc = new StateAccumulator();
    acc.reset("ctx-1");
    acc.apply(snapshot({ logs: [entry(0, "from ctx-1")], log_version: 1 }));

    const state = acc.apply(
      snapshot({ context: "ctx-2", logs: [entry(0, "from ctx-2")], log_version: 1 }),
    );

    expect(state.logs.map((e) => e.content)).toEqual(["from ctx-1"]);
  });

  it("discards the log when the conversation changes", () => {
    const acc = new StateAccumulator();
    acc.reset("ctx-1");
    acc.apply(snapshot({ logs: [entry(0, "old chat")], log_version: 1 }));

    acc.reset("ctx-2");
    const state = acc.apply(snapshot({ context: "ctx-2", logs: [entry(0, "new chat")] }));

    expect(state.logs.map((e) => e.content)).toEqual(["new chat"]);
  });

  it("keeps the last known conversation list when a push omits it", () => {
    // Defensive: the server's snapshot schema always carries `contexts`, so this
    // guards a malformed or partial push rather than normal operation. Note the
    // key is deleted, not set to [] -- an empty list is a real answer meaning
    // "no conversations" and must be allowed to replace.
    const acc = new StateAccumulator();
    acc.reset("ctx-1");
    acc.apply(snapshot({ contexts: [{ id: "ctx-1", name: "First" }] }));

    const partial = snapshot({ logs: [entry(0, "hi")] });
    delete partial.contexts;

    const state = acc.apply(partial);
    expect(state.contexts).toHaveLength(1);
  });

  it("drops the old transcript when the log guid changes (history cleared)", () => {
    const acc = new StateAccumulator();
    acc.reset("ctx-1");
    acc.apply(snapshot({ log_guid: "a", logs: [entry(0, "old 0"), entry(1, "old 1"), entry(2, "old 2")] }));

    // The push after a clear carries no entries at all.
    expect(acc.apply(snapshot({ log_guid: "b", logs: [] })).logs).toEqual([]);

    // And the first new entry must not sit beside the old ones with higher numbers.
    const state = acc.apply(snapshot({ log_guid: "b", logs: [entry(0, "new 0")] }));
    expect(state.logs.map((e) => e.content)).toEqual(["new 0"]);
  });

  it("accepts an empty conversation list as a real answer", () => {
    const acc = new StateAccumulator();
    acc.reset("ctx-1");
    acc.apply(snapshot({ contexts: [{ id: "ctx-1", name: "First" }] }));

    const state = acc.apply(snapshot({ contexts: [] }));
    expect(state.contexts).toEqual([]);
  });

  it("reports progress as inactive when a push omits the flag", () => {
    // The flag going missing must read as "finished", not as "still working
    // forever" -- a spinner that never stops is the worse failure.
    const acc = new StateAccumulator();
    acc.reset("ctx-1");
    acc.apply(snapshot({ log_progress_active: true, log_progress: "Thinking" }));

    const state = acc.apply(snapshot({ logs: [entry(0, "done")] }));
    expect(state.log_progress_active).toBe(false);
  });

  it("returns an empty but valid state before any push", () => {
    const acc = new StateAccumulator();
    acc.reset("ctx-1");
    const state = acc.current();
    expect(state.context).toBe("ctx-1");
    expect(state.logs).toEqual([]);
    expect(state.log_progress_active).toBe(false);
  });
});


describe("unwrapSnapshot", () => {
  it("reads the payload out of the server's envelope", () => {
    // helpers/ws_manager.py wraps every emission as {ts, correlationId,
    // eventId, handlerId, data}. Reading `envelope.snapshot` finds nothing and
    // the screen stays empty while the socket looks perfectly healthy -- which
    // is exactly how this was shipped the first time.
    const envelope = {
      ts: "2026-08-23T00:00:00Z",
      correlationId: "c1",
      eventId: "e1",
      handlerId: "ws_webui",
      data: { runtime_epoch: "r1", seq: 3, snapshot: { context: "ctx-1", logs: [] } },
    };
    expect(unwrapSnapshot(envelope)?.context).toBe("ctx-1");
  });

  it("also accepts an unwrapped payload", () => {
    // The envelope belongs to the connection manager, the snapshot to the state
    // monitor. One changing without the other should degrade to still working.
    expect(unwrapSnapshot({ snapshot: { context: "ctx-2" } })?.context).toBe("ctx-2");
  });

  it("returns null for anything without a snapshot", () => {
    expect(unwrapSnapshot(null)).toBeNull();
    expect(unwrapSnapshot(undefined)).toBeNull();
    expect(unwrapSnapshot({})).toBeNull();
    expect(unwrapSnapshot({ data: { seq: 1 } })).toBeNull();
  });
});

describe("forContext", () => {
  it("withholds another conversation's transcript but keeps the list", () => {
    const held = new StateAccumulator();
    held.reset("ctx-1");
    const state = held.apply(snapshot({ contexts: [{ id: "ctx-1" }], logs: [entry(0, "a")], log_progress_active: true }));

    const shown = forContext(state, "ctx-2");
    expect(shown?.logs).toEqual([]);
    expect(shown?.log_progress_active).toBe(false);
    expect(shown?.contexts).toHaveLength(1);
    expect(forContext(state, "ctx-1")).toBe(state);
    expect(forContext(state, "")).toBe(state);
  });
});

describe("pickChatState", () => {
  it("while the socket is down, shows the newer of the last push and the poll", () => {
    // `live ?? fallback` kept showing the last push forever after a drop, so
    // polling updates never appeared even though they were being fetched.
    const live = {
      context: "ctx-1",
      contexts: [],
      logs: [entry(0, "from push")],
      log_guid: "g",
      log_version: 2,
      log_progress: "",
      log_progress_active: false,
      paused: false,
    };
    const fallback = {
      ...live,
      logs: [entry(0, "from push"), entry(1, "from poll")],
      log_version: 3,
    };
    expect(pickChatState(live, fallback, "offline")?.log_version).toBe(3);
    expect(pickChatState(live, fallback, "live")?.log_version).toBe(2);
  });
});

describe("StateAccumulator notifications", () => {
  const note = (no: number, read = false) => ({ no, id: `n${no}`, title: `note ${no}`, read });

  it("keeps a notification when the next push carries none, and takes a later change by id", () => {
    // Seen live: the bell showed a new notification only after a reload, because the push after it carried
    // an empty list and the list was replaced rather than added to.
    const acc = new StateAccumulator();
    acc.reset("c1");
    acc.apply({ context: "c1", notifications: [note(0)], notifications_guid: "g1" });
    expect(acc.apply({ context: "c1", notifications: [], notifications_guid: "g1" }).notifications).toHaveLength(1);
    acc.apply({ context: "c1", notifications: [note(1)], notifications_guid: "g1" });
    const merged = acc.apply({ context: "c1", notifications: [note(0, true)], notifications_guid: "g1" }).notifications!;
    expect(merged.map((n) => n.id)).toEqual(["n0", "n1"]);
    expect((merged[0] as { read?: boolean }).read).toBe(true);
  });

  it("starts again when the list was cleared", () => {
    const acc = new StateAccumulator();
    acc.reset("c1");
    acc.apply({ context: "c1", notifications: [note(0), note(1)], notifications_guid: "g1" });
    expect(acc.apply({ context: "c1", notifications: [], notifications_guid: "g2" }).notifications).toEqual([]);
  });
});
