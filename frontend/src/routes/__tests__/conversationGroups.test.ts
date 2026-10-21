import { describe, expect, it } from "vitest";

import type { ChatContext } from "@/api/chat";
import {
  descendantsOf,
  flattenVisible,
  organiseConversations,
} from "../conversationGroups";

// Local time, so the day buckets do not depend on the machine's zone.
const NOW = new Date(2026, 9, 15, 12, 0, 0);
const at = (daysAgo: number, hour = 9) =>
  new Date(2026, 9, 15 - daysAgo, hour, 0, 0).toISOString();
const ctx = (id: string, extra: Partial<ChatContext> = {}): ChatContext => ({ id, ...extra });
const child = (id: string, parent: string, extra: Partial<ChatContext> = {}): ChatContext =>
  ctx(id, { parent_context_kind: "subordinate", parent_context_id: parent, ...extra });

describe("organiseConversations", () => {
  it("nests children under a present parent, newest first", () => {
    const groups = organiseConversations(
      [
        ctx("p", { last_message: at(0, 10) }),
        child("c1", "p", { last_message: at(0, 8) }),
        child("c2", "p", { last_message: at(0, 9) }),
      ],
      NOW,
    );
    const rows = groups.flatMap((g) => g.rows);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.context.id).toBe("p");
    expect(rows[0]?.children.map((c) => c.id)).toEqual(["c2", "c1"]);
  });

  it("attaches a sub-agent of a sub-agent to the top-level ancestor", () => {
    const rows = organiseConversations(
      [ctx("p", { last_message: at(0, 10) }), child("c", "p"), child("g", "c")],
      NOW,
    ).flatMap((g) => g.rows);
    expect(rows.map((r) => r.context.id)).toEqual(["p"]);
    expect(rows[0]?.children.map((c) => c.id).sort()).toEqual(["c", "g"]);
  });

  it("does not loop or lose conversations on a malformed parent cycle", () => {
    const rows = organiseConversations([child("a", "b"), child("b", "a")], NOW).flatMap(
      (g) => g.rows,
    );
    expect(rows.map((r) => r.context.id).sort()).toEqual(["a", "b"]);
  });

  it("keeps orphans, and non-subordinate children, top-level", () => {
    const rows = organiseConversations(
      [
        ctx("a", { last_message: at(0, 10) }),
        child("orphan", "gone", { last_message: at(0, 9) }),
        ctx("x", { parent_context_id: "a", last_message: at(0, 8) }),
      ],
      NOW,
    ).flatMap((g) => g.rows);
    expect(rows.map((r) => r.context.id)).toEqual(["a", "orphan", "x"]);
    expect(rows.every((r) => r.children.length === 0)).toBe(true);
  });

  it("sorts by activity, newest first, ties in input order", () => {
    const rows = organiseConversations(
      [
        ctx("old", { last_message: at(0, 7) }),
        ctx("new", { last_message: at(0, 11) }),
        ctx("tie1", { created_at: at(0, 9) }),
        ctx("tie2", { created_at: at(0, 9) }),
      ],
      NOW,
    ).flatMap((g) => g.rows);
    expect(rows.map((r) => r.context.id)).toEqual(["new", "tie1", "tie2", "old"]);
  });

  it("ranks a parent by its newest child", () => {
    const rows = organiseConversations(
      [
        ctx("p", { last_message: at(0, 6) }),
        ctx("other", { last_message: at(0, 9) }),
        child("c", "p", { last_message: at(0, 11) }),
      ],
      NOW,
    ).flatMap((g) => g.rows);
    expect(rows.map((r) => r.context.id)).toEqual(["p", "other"]);
  });

  it("buckets by local calendar day, in order, omitting empty ones", () => {
    const groups = organiseConversations(
      [
        ctx("earlier", { last_message: at(30) }),
        ctx("week", { last_message: at(3) }),
        ctx("yday", { last_message: at(1, 23) }),
        ctx("today", { last_message: at(0, 0) }),
      ],
      NOW,
    );
    expect(groups.map((g) => g.bucket)).toEqual(["Today", "Yesterday", "This week", "Earlier"]);
    expect(groups.map((g) => g.rows[0]?.context.id)).toEqual(["today", "yday", "week", "earlier"]);

    const onlyToday = organiseConversations([ctx("t", { last_message: at(0, 8) })], NOW);
    expect(onlyToday.map((g) => g.bucket)).toEqual(["Today"]);
  });

  it("puts missing or invalid timestamps in Earlier", () => {
    const groups = organiseConversations([ctx("none"), ctx("bad", { created_at: "nope" })], NOW);
    expect(groups.map((g) => g.bucket)).toEqual(["Earlier"]);
    expect(groups[0]?.rows).toHaveLength(2);
  });

  it("falls back to created_at when last_message is absent", () => {
    const groups = organiseConversations([ctx("a", { created_at: at(0, 8) })], NOW);
    expect(groups[0]?.bucket).toBe("Today");
  });
});

describe("flattenVisible and descendantsOf", () => {
  it("lists parents then expanded children in order", () => {
    const groups = organiseConversations(
      [
        ctx("p", { last_message: at(0, 10) }),
        child("c1", "p", { last_message: at(0, 8) }),
        child("c2", "p", { last_message: at(0, 9) }),
        ctx("q", { last_message: at(0, 7) }),
      ],
      NOW,
    );
    expect(flattenVisible(groups, new Set()).map((c) => c.id)).toEqual(["p", "q"]);
    expect(flattenVisible(groups, new Set(["p"])).map((c) => c.id)).toEqual([
      "p",
      "c2",
      "c1",
      "q",
    ]);
  });

  it("descendantsOf returns the row's children", () => {
    const row = organiseConversations(
      [ctx("p", { last_message: at(0, 10) }), child("c", "p")],
      NOW,
    )[0]!.rows[0]!;
    expect(descendantsOf(row).map((c) => c.id)).toEqual(["c"]);
  });
});
