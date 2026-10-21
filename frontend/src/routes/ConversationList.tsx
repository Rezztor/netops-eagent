/**
 * The conversation list, and finding something in it.
 *
 * Two tiers, because they cost different amounts. Typing filters the titles
 * immediately from the list already loaded — no request, no waiting, and it is
 * what most searches want. Content search asks the server, debounced, and merges
 * its hits in underneath.
 *
 * When not searching, conversations are nested (sub-agents under parents),
 * sorted by recent activity, and grouped by local calendar day.
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";

import {
  removeChats,
  searchChats,
  type ChatContext,
  type ChatSearchHit,
} from "@/api/chat";
import { Button, useToast } from "@/components";
import {
  descendantsOf,
  flattenVisible,
  organiseConversations,
  type ConversationRow,
} from "./conversationGroups";
import { ConversationDeleteDialog } from "./ConversationDeleteDialog";
import styles from "./chat.module.css";

/** Long enough that a request is worth making, short enough not to feel laggy. */
const DEBOUNCE_MS = 300;
const MIN_QUERY = 2;
const EXPANDED_KEY = "eagent.chat.expanded";

function loadExpanded(): Set<string> {
  try {
    const raw = localStorage.getItem(EXPANDED_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((id): id is string => typeof id === "string"));
  } catch {
    return new Set();
  }
}

function saveExpanded(ids: Set<string>) {
  try {
    localStorage.setItem(EXPANDED_KEY, JSON.stringify([...ids]));
  } catch {
    /* page must work without storage */
  }
}

function rowShell(id: string, activeId: string, extra = ""): string {
  const parts = [styles.chatItemRow ?? ""];
  if (id === activeId) parts.push(styles.chatItemActive ?? "");
  if (extra) parts.push(extra);
  return parts.filter(Boolean).join(" ");
}

function RowMeta({
  context,
  relativeTime,
}: {
  context: ChatContext;
  relativeTime: (value: string | undefined) => string;
}) {
  const when = relativeTime(context.last_message ?? context.created_at);
  const label = context.agent_profile_label;
  const parts = [label, when].filter(Boolean);
  if (parts.length === 0) return null;
  return <span className={styles.chatItemMeta}>{parts.join(" · ")}</span>;
}

function parentCheckState(
  row: ConversationRow,
  selected: Set<string>,
): { checked: boolean; indeterminate: boolean } {
  const kids = descendantsOf(row);
  if (kids.length === 0) {
    return { checked: selected.has(row.context.id), indeterminate: false };
  }
  const kidSelected = kids.filter((c) => selected.has(c.id)).length;
  const parentOn = selected.has(row.context.id);
  if (parentOn && kidSelected === kids.length) {
    return { checked: true, indeterminate: false };
  }
  if (kidSelected === 0 && !parentOn) {
    return { checked: false, indeterminate: false };
  }
  if (parentOn && kidSelected === 0) {
    return { checked: true, indeterminate: false };
  }
  return { checked: false, indeterminate: true };
}

export function ConversationList({
  contexts,
  activeId,
  onPick,
  relativeTime,
  selectMode = false,
  onSelectModeChange,
  onRemoved,
}: {
  contexts: ChatContext[];
  activeId: string;
  onPick: (id: string) => void;
  relativeTime: (value: string | undefined) => string;
  selectMode?: boolean;
  onSelectModeChange?: (next: boolean) => void;
  /** Called after a successful bulk delete with the removed ids (and remaining list). */
  onRemoved?: (removed: string[], remaining: ChatContext[]) => void;
}) {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ChatSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(loadExpanded);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const lastClickedId = useRef<string | null>(null);

  const term = query.trim();
  const searchable = term.length >= MIN_QUERY;

  const byTitle = useMemo(() => {
    if (!searchable) return contexts;
    const needle = term.toLowerCase();
    return contexts.filter((c) => String(c.name ?? "").toLowerCase().includes(needle));
  }, [contexts, term, searchable]);

  const groups = useMemo(
    () => (searchable ? [] : organiseConversations(contexts)),
    [contexts, searchable],
  );

  const rowsById = useMemo(() => {
    const map = new Map<string, ConversationRow>();
    for (const group of groups) {
      for (const row of group.rows) map.set(row.context.id, row);
    }
    return map;
  }, [groups]);

  const byId = useMemo(() => new Map(contexts.map((c) => [c.id, c])), [contexts]);

  const effectiveExpanded = useMemo(() => {
    const next = new Set(expandedIds);
    for (const group of groups) {
      for (const row of group.rows) {
        if (row.children.some((c) => c.id === activeId)) next.add(row.context.id);
      }
    }
    return next;
  }, [expandedIds, groups, activeId]);

  const visibleOrder = useMemo(() => {
    if (searchable) return byTitle;
    return flattenVisible(groups, effectiveExpanded);
  }, [searchable, byTitle, groups, effectiveExpanded]);

  useEffect(() => {
    if (!searchable) {
      setHits([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    let live = true;
    const timer = setTimeout(() => {
      void searchChats(term)
        .then((result) => {
          if (live) setHits(result.chats ?? []);
        })
        .catch(() => {
          if (live) setHits([]);
        })
        .finally(() => {
          if (live) setSearching(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [term, searchable]);

  useEffect(() => {
    if (!selectMode) {
      setSelected(new Set());
      setConfirmOpen(false);
      lastClickedId.current = null;
    }
  }, [selectMode]);

  useEffect(() => {
    if (!selectMode) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onSelectModeChange?.(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selectMode, onSelectModeChange]);

  const titleIds = new Set(byTitle.map((c) => c.id));
  const contentOnly = hits.filter((hit) => !titleIds.has(hit.id));
  const nothing = searchable && byTitle.length === 0 && contentOnly.length === 0 && !searching;

  const parentsWithKids = useMemo(
    () => groups.flatMap((g) => g.rows).filter((r) => r.children.length > 0),
    [groups],
  );

  function setExpanded(next: Set<string>) {
    setExpandedIds(next);
    saveExpanded(next);
  }

  function isExpanded(parentId: string): boolean {
    return effectiveExpanded.has(parentId);
  }

  function toggleExpanded(parentId: string) {
    const next = new Set(expandedIds);
    if (next.has(parentId)) next.delete(parentId);
    else next.add(parentId);
    // Keep auto-expanded active parents open even if user collapses — only if
    // active is not a descendant; otherwise collapsing should stick via override.
    // Persisted set is source of truth; auto-expand is display-only via effectiveExpanded.
    // To collapse while active child: remove from set and... auto still forces open.
    // Spec: auto-expand when activeId is descendant — so we cannot collapse while
    // viewing a child. That's fine.
    setExpanded(next);
  }

  function expandAll() {
    setExpanded(new Set(parentsWithKids.map((r) => r.context.id)));
  }

  function collapseAll() {
    setExpanded(new Set());
  }

  function enterSelect(seedId?: string) {
    onSelectModeChange?.(true);
    if (seedId) setSelected(new Set([seedId]));
  }

  function exitSelect() {
    onSelectModeChange?.(false);
  }

  function applyToggle(ids: string[], turnOn: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (turnOn) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  function toggleRow(id: string, event: ReactMouseEvent) {
    const row = rowsById.get(id);
    const kids = row ? descendantsOf(row).map((c) => c.id) : [];
    const ids = [id, ...kids];

    if (event.shiftKey && lastClickedId.current) {
      const order = visibleOrder.map((c) => c.id);
      const a = order.indexOf(lastClickedId.current);
      const b = order.indexOf(id);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        const range = order.slice(lo, hi + 1);
        // Also pull children of any parent in range.
        const extra: string[] = [];
        for (const rid of range) {
          const r = rowsById.get(rid);
          if (r) extra.push(...descendantsOf(r).map((c) => c.id));
        }
        applyToggle([...new Set([...range, ...extra])], true);
        lastClickedId.current = id;
        return;
      }
    }

    const turnOn = !selected.has(id);
    applyToggle(ids, turnOn);
    lastClickedId.current = id;
  }

  function onRowClick(context: ChatContext, event: ReactMouseEvent) {
    if (event.metaKey || event.ctrlKey) {
      event.preventDefault();
      if (!selectMode) enterSelect(context.id);
      else toggleRow(context.id, event);
      return;
    }
    if (selectMode) {
      event.preventDefault();
      toggleRow(context.id, event);
      return;
    }
    onPick(context.id);
  }

  function onRowKeyDown(context: ChatContext, event: ReactKeyboardEvent, hasKids: boolean) {
    if (selectMode) return;
    if (hasKids && event.key === "ArrowRight") {
      event.preventDefault();
      if (!isExpanded(context.id)) {
        const next = new Set(expandedIds);
        next.add(context.id);
        setExpanded(next);
      }
    } else if (hasKids && event.key === "ArrowLeft") {
      event.preventDefault();
      if (expandedIds.has(context.id)) {
        const next = new Set(expandedIds);
        next.delete(context.id);
        setExpanded(next);
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      onPick(context.id);
    }
  }

  const selectedContexts = [...selected]
    .map((id) => byId.get(id))
    .filter((c): c is ChatContext => Boolean(c));

  const selectedSubagentCount = selectedContexts.filter(
    (c) => c.parent_context_kind === "subordinate",
  ).length;

  const allVisibleIds = searchable
    ? byTitle.map((c) => c.id)
    : visibleOrder.map((c) => c.id);
  const selectedVisible = allVisibleIds.filter((id) => selected.has(id)).length;
  const allSelected = allVisibleIds.length > 0 && selectedVisible === allVisibleIds.length;
  const someSelected = selectedVisible > 0 && !allSelected;

  function selectAllVisible() {
    setSelected(new Set(allVisibleIds));
  }

  function toggleSelectAll() {
    if (allSelected) setSelected(new Set());
    else selectAllVisible();
  }

  async function confirmDelete(includeSubagents: boolean) {
    const ids = [...selected];
    try {
      const result = await removeChats(ids, includeSubagents);
      const removed = result.removed ?? [];
      const failed = result.failed ?? [];
      if (failed.length === 0) {
        toast.success(
          `Deleted ${removed.length} conversation${removed.length === 1 ? "" : "s"}`,
        );
      } else {
        const reason = failed[0]?.error || "unknown error";
        toast.error(
          `${removed.length} deleted, ${failed.length} could not be removed: ${reason}`,
        );
      }
      setConfirmOpen(false);
      exitSelect();
      const removedSet = new Set(removed);
      const remaining = contexts.filter((c) => !removedSet.has(c.id));
      onRemoved?.(removed, remaining);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
      setConfirmOpen(false);
    }
  }

  function renderFoldControl(row: ConversationRow) {
    const n = row.children.length;
    if (n === 0) return null;
    const open = isExpanded(row.context.id);
    const selectedKids = row.children.filter((c) => selected.has(c.id)).length;
    const label =
      selectMode && selectedKids > 0 && selectedKids < n
        ? `${selectedKids} of ${n}`
        : `${n} sub-agents`;
    return (
      <button
        type="button"
        className={styles.chatFoldControl}
        aria-expanded={open}
        aria-label={`${n} sub-agents`}
        onClick={(event) => {
          event.stopPropagation();
          toggleExpanded(row.context.id);
        }}
      >
        <span className={open ? styles.chatChevronOpen : styles.chatChevron} aria-hidden>
          ▸
        </span>
        <span className={styles.chatSubagentChip}>{label}</span>
      </button>
    );
  }

  function renderCheckbox(context: ChatContext, row?: ConversationRow) {
    const state = row
      ? parentCheckState(row, selected)
      : { checked: selected.has(context.id), indeterminate: false };
    const title = context.name || "Untitled conversation";
    return (
      <input
        type="checkbox"
        className={styles.chatSelectBox}
        checked={state.checked}
        aria-label={title}
        ref={(node) => {
          if (node) node.indeterminate = state.indeterminate;
        }}
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => {
          const kids = row ? descendantsOf(row).map((c) => c.id) : [];
          applyToggle([context.id, ...kids], event.target.checked);
          lastClickedId.current = context.id;
        }}
      />
    );
  }

  function renderRow(context: ChatContext, extraClass = "", row?: ConversationRow) {
    const title = context.name || "Untitled conversation";
    const hasKids = Boolean(row && row.children.length > 0);
    return (
      <div
        key={context.id}
        className={rowShell(context.id, selectMode ? "" : activeId, extraClass)}
        onClick={(event) => onRowClick(context, event)}
        onKeyDown={(event) => onRowKeyDown(context, event, hasKids)}
        role="button"
        tabIndex={0}
        title={title}
      >
        {selectMode && renderCheckbox(context, row)}
        {row && renderFoldControl(row)}
        <span className={styles.chatItemBody}>
          <span className={styles.chatItemTitle}>{title}</span>
          <RowMeta context={context} relativeTime={relativeTime} />
        </span>
      </div>
    );
  }

  return (
    <>
      {selectMode && (
        <div className={styles.selectBar}>
          <div className={styles.selectBarTop}>
            <input
              type="checkbox"
              className={styles.chatSelectBox}
              aria-label="Select all"
              checked={allSelected}
              ref={(node) => {
                if (node) node.indeterminate = someSelected;
              }}
              onChange={toggleSelectAll}
            />
            <span className={styles.selectCount}>
              {`${selected.size} selected`}
            </span>
            <Button
              variant="danger"
              disabled={selected.size === 0}
              onClick={() => setConfirmOpen(true)}
            >
              Delete
            </Button>
            <Button onClick={exitSelect}>Cancel</Button>
          </div>
          <div className={styles.selectBarBottom}>
            {selectedSubagentCount > 0 ? (
              <span>{`${selectedSubagentCount} sub-agent chat${selectedSubagentCount === 1 ? "" : "s"} included`}</span>
            ) : (
              <span />
            )}
            <button type="button" className={styles.selectAllLink} onClick={selectAllVisible}>
              Select all
            </button>
          </div>
        </div>
      )}

      <input
        type="search"
        className={styles.chatSearch}
        placeholder="Search conversations…"
        aria-label="Search conversations"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      {!searchable && parentsWithKids.length > 0 && (
        <div className={styles.expandLinks}>
          <button type="button" className={styles.expandLink} onClick={expandAll}>
            Expand all
          </button>
          <span aria-hidden> / </span>
          <button type="button" className={styles.expandLink} onClick={collapseAll}>
            Collapse all
          </button>
        </div>
      )}

      <div className={styles.chatList}>
        {contexts.length === 0 && (
          <p className={styles.chatListEmpty}>No conversations yet.</p>
        )}

        {nothing && <p className={styles.chatListEmpty}>Nothing matches “{term}”.</p>}

        {!searchable &&
          groups.map((group) => (
            <div key={group.bucket}>
              <p className={styles.chatListDivider}>{group.bucket}</p>
              {group.rows.map((row) => (
                <div key={row.context.id}>
                  {renderRow(row.context, "", row)}
                  {row.children.length > 0 &&
                    isExpanded(row.context.id) &&
                    row.children.map((child) =>
                      renderRow(child, styles.chatItemChild ?? ""),
                    )}
                </div>
              ))}
            </div>
          ))}

        {searchable && byTitle.map((context) => renderRow(context))}

        {contentOnly.length > 0 && (
          <p className={styles.chatListDivider}>
            {searching ? "Searching what was said…" : "Mentioned in"}
          </p>
        )}

        {contentOnly.map((hit) => (
          <button
            key={hit.id}
            type="button"
            className={rowShell(hit.id, selectMode ? "" : activeId)}
            onClick={(event) => {
              if (selectMode || event.metaKey || event.ctrlKey) {
                if (!selectMode) enterSelect(hit.id);
                else {
                  applyToggle([hit.id], !selected.has(hit.id));
                  lastClickedId.current = hit.id;
                }
                return;
              }
              onPick(hit.id);
            }}
          >
            {selectMode && (
              <input
                type="checkbox"
                className={styles.chatSelectBox}
                checked={selected.has(hit.id)}
                aria-label={hit.name}
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => applyToggle([hit.id], event.target.checked)}
              />
            )}
            <span className={styles.chatItemBody}>
              {hit.name}
              <span className={styles.chatItemSnippet}>{hit.snippet}</span>
              <span className={styles.chatItemMeta}>
                {hit.hits} mention{hit.hits === 1 ? "" : "s"}
              </span>
            </span>
          </button>
        ))}

        {searching && contentOnly.length === 0 && byTitle.length > 0 && (
          <p className={styles.chatListEmpty}>Searching what was said…</p>
        )}
      </div>

      {confirmOpen && (
        <ConversationDeleteDialog
          selected={selectedContexts}
          rowsById={rowsById}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={confirmDelete}
        />
      )}
    </>
  );
}
