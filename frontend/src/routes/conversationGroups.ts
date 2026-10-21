/**
 * Pure grouping/sorting for the conversation sidebar.
 * Nest sub-agents under parents, sort by activity, bucket by local day.
 */

import type { ChatContext } from "@/api/chat";

export type DayBucket = "Today" | "Yesterday" | "This week" | "Earlier";

export interface ConversationRow {
  context: ChatContext;
  children: ChatContext[];
}

export interface ConversationGroup {
  bucket: DayBucket;
  rows: ConversationRow[];
}

const BUCKET_ORDER: DayBucket[] = ["Today", "Yesterday", "This week", "Earlier"];
const DAY_MS = 86_400_000;

function activity(context: ChatContext): number {
  const raw = context.last_message ?? context.created_at;
  if (typeof raw !== "string" || !raw) return 0;
  const t = Date.parse(raw);
  return Number.isFinite(t) ? t : 0;
}

function startOfLocalDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function bucketFor(activityMs: number, now: Date): DayBucket {
  if (activityMs === 0) return "Earlier";
  const ageDays = Math.floor((startOfLocalDay(now) - startOfLocalDay(new Date(activityMs))) / DAY_MS);
  if (ageDays === 0) return "Today";
  if (ageDays === 1) return "Yesterday";
  if (ageDays >= 2 && ageDays < 7) return "This week";
  return "Earlier";
}

function rowActivity(row: ConversationRow): number {
  let max = activity(row.context);
  for (const child of row.children) {
    const a = activity(child);
    if (a > max) max = a;
  }
  return max;
}

export function organiseConversations(
  contexts: ChatContext[],
  now: Date = new Date(),
): ConversationGroup[] {
  const byId = new Map(contexts.map((c) => [c.id, c]));
  const childIds = new Set<string>();
  const childrenByParent = new Map<string, ChatContext[]>();

  // A sub-agent's parent may itself be a sub-agent, so each child is attached to
  // its top-level ancestor. `seen` stops a malformed parent cycle from looping
  // forever; a context caught in one stays top-level.
  const parentOf = (c: ChatContext): string | undefined => {
    const parentId = c.parent_context_id;
    return c.parent_context_kind === "subordinate" && typeof parentId === "string" && byId.has(parentId)
      ? parentId
      : undefined;
  };
  for (const c of contexts) {
    let rootId = parentOf(c);
    if (rootId === undefined) continue;
    const seen = new Set<string>([c.id]);
    for (let next = parentOf(byId.get(rootId)!); next !== undefined; next = parentOf(byId.get(rootId)!)) {
      if (seen.has(next)) { rootId = ""; break; }
      seen.add(rootId);
      rootId = next;
    }
    if (rootId === "") continue;
    childIds.add(c.id);
    const list = childrenByParent.get(rootId) ?? [];
    list.push(c);
    childrenByParent.set(rootId, list);
  }

  for (const kids of childrenByParent.values()) {
    kids.sort((a, b) => activity(b) - activity(a));
  }

  const rows: ConversationRow[] = [];
  for (const c of contexts) {
    if (childIds.has(c.id)) continue;
    rows.push({ context: c, children: childrenByParent.get(c.id) ?? [] });
  }

  const decorated = rows.map((row, index) => ({ row, index, act: rowActivity(row) }));
  decorated.sort((a, b) => b.act - a.act || a.index - b.index);

  const buckets = new Map<DayBucket, ConversationRow[]>(BUCKET_ORDER.map((b) => [b, []]));
  for (const { row, act } of decorated) {
    buckets.get(bucketFor(act, now))!.push(row);
  }

  return BUCKET_ORDER.map((bucket) => ({ bucket, rows: buckets.get(bucket)! })).filter(
    (g) => g.rows.length > 0,
  );
}

/** Visible row order for the sidebar (parents, then expanded children). */
export function flattenVisible(
  groups: ConversationGroup[],
  expandedIds: ReadonlySet<string>,
): ChatContext[] {
  const out: ChatContext[] = [];
  for (const group of groups) {
    for (const row of group.rows) {
      out.push(row.context);
      if (expandedIds.has(row.context.id) && row.children.length > 0) {
        out.push(...row.children);
      }
    }
  }
  return out;
}

/** Direct children of a parent row (already flattened to the top-level ancestor). */
export function descendantsOf(row: ConversationRow): ChatContext[] {
  return row.children;
}
