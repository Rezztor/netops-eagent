/**
 * Chat.
 *
 * Live over Socket.IO, with polling as a fallback rather than as the mechanism.
 * The server pushes `state_push` envelopes carrying the same snapshot `/api/poll`
 * returns, so output appears as it is produced instead of on the next tick.
 *
 * Polling still exists for one reason: if the socket cannot connect -- a proxy
 * that does not forward upgrades, a network that blocks them -- the product
 * should degrade to slower updates rather than to no updates. The fallback only
 * runs while the connection is not live, and the header says which is in use so
 * a degraded session is visible rather than merely sluggish.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import {
  branchChat,
  compactChat,
  createChat,
  exportChat,
  getCompactionStats,
  getContextWindow,
  listCommands,
  nudge,
  poll,
  queueAdd,
  queueRemove,
  queueSend,
  removeChat,
  resetChat,
  sendMessage,
  sendMessageWithAttachments,
  setPaused,
  stop,
  type SlashCommand,
  type ChatContext,
  type LogItem,
  type PollResponse,
} from "@/api/chat";
import { Button, ErrorNote, Mono, Tag, useConfirm, useToast } from "@/components";
import { DICTATION_START_LABEL, DICTATION_STOP_LABEL, useDictation } from "@/lib/dictation";
import { isFocused, withFocus } from "@/lib/focus";
import { useUrlState } from "@/lib/useUrlState";
import { getDevice } from "@/api/inventory";
import { useLocation, useNavigate } from "react-router-dom";
import { useCan, useSession } from "@/lib/session";
import { PERMISSIONS } from "@/api/types";
import { useRealtime } from "@/lib/realtime";
import { forContext, mergeLogs, pickChatState } from "@/api/realtime";
import { ConversationList } from "./ConversationList";
import type { Match } from "@/lib/transcript";
import { FindInConversation } from "./FindInConversation";
import { ConversationPanel } from "./ConversationPanel";
import { ThreadToolbar } from "./ThreadToolbar";
import { Transcript } from "./Transcript";
import styles from "./chat.module.css";

// Only used while the socket is not live.
const FALLBACK_ACTIVE_INTERVAL = 1500;
const FALLBACK_IDLE_INTERVAL = 6000;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Anything at or before this is the backend's stand-in for "never". */
const EPOCH_SENTINEL_CUTOFF = Date.UTC(1971, 0, 1);

/**
 * "2m ago", for the conversation list.
 *
 * `AgentContext.output()` serialises a missing timestamp as the Unix epoch
 * rather than as null, so a 1970 date means the conversation has never been
 * spoken to -- not that it was last used fifty-odd years ago.
 */
function relativeTime(value: string | undefined): string {
  if (!value) return "";
  const then = new Date(value).getTime();
  if (!Number.isFinite(then) || then <= EPOCH_SENTINEL_CUTOFF) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  if (seconds < 172800) return "yesterday";
  if (seconds < 2592000) return `${Math.round(seconds / 86400)}d ago`;
  return new Date(then).toLocaleDateString();
}

/** How many commands the empty thread offers before the row becomes a list. */
const MAX_SUGGESTIONS = 6;

/** The first message of a conversation opened from a device record. */
export function askAboutDevice(name: string): string {
  return `Tell me about the device ${name}: what Inventory holds for it, its current state, and anything that needs attention.`;
}

/**
 * What a network engineer might ask first. Put in the composer, not sent: the
 * device names are examples, and the person has to make them theirs.
 */
export const EXAMPLE_PROMPTS = [
  "Show the interface status on sw-01",
  "Which devices have not had a configuration backup this week?",
  "Draft a change putting Ethernet12 on sw-01 into VLAN 20, for approval",
  "Why can 10.0.20.15 not reach 10.0.30.8 on port 443?",
];

/** The conversation spoken to most recently, else the newest. */
export function latestContext(contexts: ChatContext[]): ChatContext | undefined {
  const when = (c: ChatContext) => String(c.last_message ?? c.created_at ?? "");
  return [...contexts].sort((a, b) => when(b).localeCompare(when(a)))[0];
}

export function ChatScreen() {
  const session = useSession();
  const isAdmin = useCan(PERMISSIONS.settingsManage);
  const { confirm } = useConfirm();
  const toast = useToast();
  // The socket belongs to the application, not this screen: the same snapshot
  // also drives the notification badge in the shell.
  const { state: live, connection, setContext: setRealtimeContext, contextId } = useRealtime();
  // The conversation id lives in the URL so a reload or a shared link reopens
  // the same thread rather than the default one.
  const [urlContext, setUrlContext] = useUrlState("ctx");
  const [queued, setQueued] = useState(0);
  const [fallbackData, setFallbackData] = useState<PollResponse | null>(null);
  // The fallback poll's incremental state. Refs rather than state: they are read
  // by the next request, and making them render inputs would restart the loop.
  const fallbackLogsRef = useRef<LogItem[]>([]);
  const logCursorRef = useRef(0);
  const logGuidRef = useRef("");
  // The caught value itself, not its message: an ApiError carries the server's
  // own text, which administrators can unfold under the sentence.
  const [error, setError] = useState<unknown>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  // Card answers that arrive while a send is in flight. Dropping them spent the
  // card and lost the answer; they run when the current send finishes.
  const answerQueueRef = useRef<Array<{ text: string; answers?: string }>>([]);
  const [attachments, setAttachments] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const [commandIndex, setCommandIndex] = useState(0);
  const [inspector, setInspector] = useState<{ title: string; text: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Focused when a question card sends the reader to type their own answer.
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  // Find in this conversation. Separate from the sidebar's search, which looks
  // across conversations -- this one only ever looks at what is loaded.
  const [finding, setFinding] = useState(false);
  const [findTerm, setFindTerm] = useState("");
  const [findMatches, setFindMatches] = useState<Match[]>([]);
  const [findActive, setFindActive] = useState(0);
  const [selectMode, setSelectMode] = useState(false);
  // Narrow screens: the conversation list is a drawer over the thread, so the
  // thread and its composer get the whole height (chat.module.css).
  const [listOpen, setListOpen] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);
  const location = useLocation();
  const navigate = useNavigate();
  const focused = isFocused(location.search);
  const toggleFocus = () => navigate({ search: withFocus(location.search, !focused) });

  // Prefer pushed state while live; once the socket drops, show whichever is newer.
  const data = forContext(pickChatState(live, fallbackData, connection), contextId);
  const setContextId = setRealtimeContext;
  // Read inside the fallback loop without making it a dependency, which would
  // restart the loop on every update. Synced in an effect rather than during
  // render: a ref written while rendering is not a render input, and React
  // rightly refuses it.
  const liveRef = useRef(false);
  const activeRef = useRef(false);

  const tick = useCallback(async () => {
    try {
      const next = await poll({
        context: contextId,
        // A cursor, not a constant. `log_from` indexes the backend's stream of
        // *changed* entries, so asking from the last `log_version` still brings
        // back edits to entries already seen -- a tool line updated with its
        // result comes through like any other change.
        //
        // This asked from 0 every time. On a long conversation that is the
        // entire transcript, kvps and all, re-sent and re-rendered on every
        // poll: ~475 KB a tick on one measured here, enough to stall the tab.
        // The socket path has always merged incrementally; only the fallback
        // did not.
        logFrom: logCursorRef.current,
        timezone: session.preferences.timezone,
      });

      // A new guid means a different log, not more of the same one -- another
      // conversation, or one that was reset. Merging across that would splice
      // two transcripts together by index.
      const sameLog = logGuidRef.current === next.log_guid;
      const merged = sameLog ? mergeLogs(fallbackLogsRef.current, next.logs ?? []) : (next.logs ?? []);
      fallbackLogsRef.current = merged;
      logGuidRef.current = next.log_guid ?? "";
      logCursorRef.current = next.log_version ?? 0;

      setFallbackData({ ...next, logs: merged });
      setError(null);
      // The backend may hand back a context we did not ask for -- it creates one
      // when none was named. Adopt it so the next poll is scoped correctly.
      if (!contextId && next.context) setContextId(next.context);
    } catch (caught) {
      setError(caught);
    }
  }, [contextId, session.preferences.timezone, setContextId]);

  useEffect(() => {
    liveRef.current = connection === "live";
  }, [connection]);

  // Adopt a conversation named in the URL (reload, bookmark, shared link).
  useEffect(() => {
    if (urlContext && urlContext !== contextId) setContextId(urlContext);
    // Only react to the URL changing; the reverse sync is the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlContext]);

  // Reflect the selected conversation back into the URL.
  useEffect(() => {
    setUrlContext(contextId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextId]);

  // A conversation change invalidates the cursor: `log_from` indexes one log's
  // stream of changes, and carrying a position across conversations would ask
  // the new one to start partway through a history it does not have.
  // Draft, attachments, queue count and the context-window inspector are also
  // per-conversation: leaving them set shows the previous chat's unfinished work.
  // Not when the screen had no conversation yet: the first poll and the first
  // send give the one on screen its id, and what is being typed belongs to it.
  const leftContextRef = useRef(contextId);
  useEffect(() => {
    fallbackLogsRef.current = [];
    logCursorRef.current = 0;
    logGuidRef.current = "";
    if (leftContextRef.current) {
      setQueued(0);
      setDraft("");
      setAttachments([]);
      setInspector(null);
    }
    leftContextRef.current = contextId;
    if (askDraftRef.current) {
      setDraft(askDraftRef.current);
      askDraftRef.current = "";
    }
  }, [contextId]);

  // "Ask about this device" on a device record arrives as ?device=<inventory id>: a new conversation with a question
  // naming the device typed in, for the engineer to send. Only an id travels in the link, and the name is read from
  // Inventory, so a crafted link cannot pre-type text of its own; an id Inventory does not know is ignored. Once per
  // id, so a re-run effect does not open a second conversation.
  const [askDevice, setAskDevice] = useUrlState("device");
  const askedRef = useRef("");
  const askDraftRef = useRef("");
  // While the device is looked up, the latest conversation is not opened first.
  const [askingDevice, setAskingDevice] = useState(false);
  useEffect(() => {
    if (!askDevice || askedRef.current === askDevice) return;
    askedRef.current = askDevice;
    setAskDevice("");
    setAskingDevice(true);
    getDevice(askDevice)
      .then(async (found) => {
        if (!found.ok || !found.device?.name) return;
        const created = await createChat(contextId);
        askDraftRef.current = askAboutDevice(found.device.name);
        setContextId(created.context);
      })
      .catch((caught) => setError(caught))
      .finally(() => setAskingDevice(false));
    // Only the URL asking matters here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askDevice]);

  // Ctrl/Cmd+F searches within the open conversation rather than firing the
  // browser's own find, which cannot see steps that are folded away.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f" && contextId) {
        event.preventDefault();
        setFinding(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [contextId]);

  // Slash commands are scoped to the conversation, so they reload with it.
  useEffect(() => {
    let cancelled = false;
    listCommands(contextId)
      .then((list) => { if (!cancelled) setCommands(list); })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setCommands([]);
        // Failing silently meant the slash-command menu just never appeared,
        // with no way to tell a broken plugin from a conversation that has none.
        toast.error(
          `Slash commands unavailable: ${caught instanceof Error ? caught.message : String(caught)}`,
        );
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextId]);

  useEffect(() => {
    activeRef.current = Boolean(data?.log_progress_active);
  }, [data?.log_progress_active]);

  // Fallback. Runs only while the socket is not live, so a working connection
  // costs nothing.
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;

    const loop = async () => {
      if (stopped) return;
      if (!liveRef.current) await tick();
      if (stopped) return;
      timer = window.setTimeout(
        loop,
        activeRef.current ? FALLBACK_ACTIVE_INTERVAL : FALLBACK_IDLE_INTERVAL,
      );
    };

    void loop();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [tick]);

  // Keep the newest entry visible, but only when the user is already at the
  // bottom -- yanking the view while they are reading history is worse than
  // making them scroll.
  useEffect(() => {
    const node = logRef.current;
    if (!node) return;
    const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 120;
    if (nearBottom) node.scrollTop = node.scrollHeight;
  }, [data?.log_version]);

  /** Run a control action and surface its failure rather than swallowing it. */
  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      // The socket pushes the new state; this only matters on the fallback path.
      if (!liveRef.current) await tick();
    } catch (caught) {
      setError(caught);
    }
  }

  function flushAnswerQueue() {
    const next = answerQueueRef.current.shift();
    if (next) void sendAnswer(next.text, next.answers);
  }

  async function onSend() {
    const text = draft.trim();
    // A message with only attachments is legitimate; one with neither is not.
    if ((!text && attachments.length === 0) || sending) return;
    setSending(true);
    setError(null);
    try {
      const result =
        attachments.length > 0
          ? await sendMessageWithAttachments(text, contextId, attachments)
          : await sendMessage(text, contextId);
      setDraft("");
      setAttachments([]);
      if (result.context && result.context !== contextId) setContextId(result.context);
      if (!liveRef.current) await tick();
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
      flushAnswerQueue();
    }
  }

  /** Send a card's answer without disturbing whatever is in the composer.
   *
   * `answers` carries the card's id, so the record says which question this was
   * an answer to rather than leaving it to adjacency. */
  async function sendAnswer(text: string, answers?: string) {
    if (!text.trim()) return;
    if (sending) {
      answerQueueRef.current.push(answers === undefined ? { text } : { text, answers });
      return;
    }
    setSending(true);
    setError(null);
    try {
      const result = await sendMessage(text, contextId, answers);
      if (result.context && result.context !== contextId) setContextId(result.context);
      if (!liveRef.current) await tick();
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
      flushAnswerQueue();
    }
  }

  // Dictated phrases are appended to whatever is already typed rather than
  // replacing it: speech is an addition to the draft, not a new one.
  const appendDictated = useCallback((text: string) => {
    setDraft((current) => (current.trim() ? `${current.trimEnd()} ${text}` : text));
  }, []);
  const dictation = useDictation(appendDictated, setError);

  function addFiles(incoming: FileList | null) {
    if (!incoming || incoming.length === 0) return;
    setAttachments((current) => [...current, ...Array.from(incoming)]);
  }

  // The menu opens only while the draft is a single leading slash-word: once
  // there is an argument the user is typing a command, not choosing one.
  const commandQuery = /^\/(\S*)$/.exec(draft)?.[1];
  const commandMatches =
    commandQuery === undefined
      ? []
      : commands
          .filter((command) => command.name.toLowerCase().startsWith(commandQuery.toLowerCase()))
          .slice(0, 8);

  function applyCommand(command: SlashCommand) {
    setDraft(`/${command.name} `);
    setCommandIndex(0);
  }

  async function onCompact() {
    // Best-effort preview of what compaction is working with, so "are you sure"
    // shows something concrete rather than a generic warning.
    let preview: ReactNode = null;
    try {
      const result = await getCompactionStats(contextId);
      const numeric = Object.entries(result.stats ?? {}).filter(
        ([, value]) => typeof value === "number" || typeof value === "string",
      );
      if (numeric.length) {
        preview = (
          <ul style={{ margin: "var(--space-2) 0 0", paddingLeft: "1.1em", color: "var(--ink-3)" }}>
            {numeric.slice(0, 6).map(([key, value]) => (
              <li key={key}>
                {key.replace(/_/g, " ")}: <Mono>{String(value)}</Mono>
              </li>
            ))}
          </ul>
        );
      }
    } catch {
      // A missing preview is not a reason to block the action.
    }
    const ok = await confirm({
      title: "Compact this conversation?",
      body: (
        <>
          Older turns are replaced by a summary to free context. This cannot be undone.
          {preview}
        </>
      ),
      confirmLabel: "Compact",
    });
    if (!ok) return;
    await act(async () => {
      const message = await compactChat(contextId);
      toast.success(message);
    });
  }

  async function onReset() {
    const ok = await confirm({
      title: "Clear this conversation's history?",
      body: "The transcript is cleared. The conversation itself is kept.",
      danger: true,
      confirmLabel: "Clear history",
    });
    if (!ok) return;
    await act(async () => {
      await resetChat(contextId);
      toast.success("Conversation history cleared.");
    });
  }

  async function onDelete() {
    const ok = await confirm({
      title: "Delete this conversation?",
      body: "The conversation and its transcript are removed. This cannot be undone.",
      danger: true,
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await removeChat(contextId);
      toast.success("Conversation deleted.");
      setContextId("");
      await tick();
    } catch (caught) {
      setError(caught);
    }
  }

  async function onQueue() {
    const text = draft.trim();
    if (!text) return;
    await act(async () => {
      await queueAdd(contextId, text);
      setDraft("");
      setQueued((count) => count + 1);
      toast.success("Queued — it sends when the agent is free.");
    });
  }

  async function onSendQueue() {
    await act(async () => {
      await queueSend(contextId);
      setQueued(0);
      toast.success("Queued messages sent.");
    });
  }

  async function onClearQueue() {
    await act(async () => {
      await queueRemove(contextId);
      setQueued(0);
      toast.success("Queue cleared.");
    });
  }

  const contexts: ChatContext[] = data?.contexts ?? [];
  // Opening Chat with nothing selected used to say "Select or start a
  // conversation" beside a list of them. Open the latest instead; with none,
  // the empty thread offers examples and the first send starts one.
  const latestId = latestContext(contexts)?.id ?? "";
  useEffect(() => {
    if (!contextId && !urlContext && !askDevice && !askingDevice && latestId) setContextId(latestId);
  }, [contextId, urlContext, askDevice, askingDevice, latestId, setContextId]);
  const logs: LogItem[] = data?.logs ?? [];
  const activeContext = contexts.find((context) => context.id === contextId);
  const running = Boolean(data?.log_progress_active);

  return (
    <div className={styles.screen}>
      {error !== null && (
        <ErrorNote error={error} details={isAdmin} onDismiss={() => setError(null)} />
      )}

      <div
        className={focused ? `${styles.layout} ${styles.layoutFocus ?? ""}` : styles.layout}
        data-list={listOpen ? "open" : undefined}
      >
        {!focused && listOpen && (
          <div className={styles.listBackdrop} aria-hidden="true" onClick={() => setListOpen(false)} />
        )}
        {!focused && (
        <aside className={styles.sidebar} id="conversation-list">
          {!selectMode && (
            <div className={styles.sidebarHead}>
              <span className={styles.sidebarTitle}>Conversations</span>
              <Button onClick={() => setSelectMode(true)}>Select</Button>
              <Button
                onClick={async () => {
                  try {
                    const created = await createChat(contextId);
                    setContextId(created.context);
                    setListOpen(false);
                    await tick();
                  } catch (caught) {
                    setError(caught);
                  }
                }}
              >
                New
              </Button>
            </div>
          )}
          <ConversationList
            contexts={contexts}
            activeId={contextId}
            onPick={(id) => {
              setContextId(id);
              setListOpen(false);
            }}
            relativeTime={relativeTime}
            selectMode={selectMode}
            onSelectModeChange={setSelectMode}
            onRemoved={(removed, remaining) => {
              if (removed.includes(contextId)) {
                setContextId(remaining[0]?.id ?? "");
              }
              void tick();
            }}
          />
        </aside>
        )}

        <section
          className={dragging ? `${styles.thread} ${styles.dropzone}` : styles.thread}
          onDragOver={(event) => {
            // Only react to a file drag; dragging selected text over the thread
            // should not offer to attach it.
            if (!event.dataTransfer.types.includes("Files")) return;
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(event) => {
            if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
            setDragging(false);
          }}
          onDrop={(event) => {
            if (!event.dataTransfer.types.includes("Files")) return;
            event.preventDefault();
            setDragging(false);
            addFiles(event.dataTransfer.files);
          }}
        >
          {!focused && (
            <button
              type="button"
              className={styles.listToggle}
              aria-expanded={listOpen}
              aria-controls="conversation-list"
              onClick={() => setListOpen((v) => !v)}
            >
              Conversations
              {activeContext?.name ? <span className={styles.listToggleName}>{activeContext.name}</span> : null}
            </button>
          )}
          {contextId && (
            <ThreadToolbar
              contextId={contextId}
              activeProfile={activeContext?.agent_profile ?? ""}
              running={Boolean(data?.log_progress_active)}
              onError={setError}
              focused={focused}
              contextTokens={Number(data?.context_tokens ?? 0)}
              onToggleFocus={toggleFocus}
              focusHref={`/app/chat${withFocus(location.search, true)}`}
              onChanged={() => {
                // The socket pushes the new snapshot; this is the fallback path.
                if (!liveRef.current) void tick();
              }}
            />
          )}

          <div className={styles.log} ref={logRef} role="log" aria-live="polite" aria-label="Conversation">
            {logs.length === 0 ? (
              <div className={styles.empty}>
                <div>Ask about your network. For example:</div>
                <div className={styles.examples}>
                  {EXAMPLE_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      className={styles.example}
                      onClick={() => {
                        setDraft(prompt);
                        composerRef.current?.focus();
                      }}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
                {commands.length > 0 && (
                  <>
                    <div>Or start with a command:</div>
                    <div className={styles.suggestions}>
                      {commands.slice(0, MAX_SUGGESTIONS).map((command) => (
                        <button
                          key={command.name}
                          type="button"
                          className={styles.suggestion}
                          onClick={() => applyCommand(command)}
                        >
                          <span className={styles.suggestionName}>/{command.name}</span>
                          {(command.description || command.argument_hint) && (
                            <span className={styles.suggestionHint}>
                              {" "}{command.description || command.argument_hint}
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            ) : (
              <>
              {finding ? (
                <FindInConversation
                  term={findTerm}
                  onTerm={(value) => {
                    setFindTerm(value);
                    // A new term makes the old position meaningless.
                    setFindActive(0);
                  }}
                  matches={findMatches}
                  active={findMatches.length ? findActive : -1}
                  onActive={setFindActive}
                  onClose={() => {
                    setFinding(false);
                    setFindTerm("");
                  }}
                />
              ) : (
                <button
                  type="button"
                  className={styles.entryAction}
                  style={{ marginBottom: "var(--space-3)" }}
                  onClick={() => setFinding(true)}
                  title="Search this conversation, including steps that are folded away"
                >
                  Find in conversation
                </button>
              )}
              <Transcript
                items={logs}
                find={finding ? { term: findTerm, activeNo: findMatches[findActive]?.no ?? null } : null}
                onMatches={setFindMatches}
                // A clicked answer is an ordinary message. That is the whole
                // reason the card needs no transport of its own.
                onAnswer={(text, answers) => void sendAnswer(text, answers)}
                onCompose={(prefill) => {
                  setDraft((current) => (current.trim() ? current : prefill));
                  composerRef.current?.focus();
                }}
                onBranch={(no) =>
                  void act(async () => {
                    // Keyed on the entry number from the transcript, not an
                    // index into the rendered array -- the two diverge the
                    // moment anything is grouped or filtered.
                    const result = await branchChat(contextId, no);
                    if (result.error) throw new Error(result.error);
                    if (result.ctxid) setContextId(result.ctxid);
                  })
                }
              />
              </>
            )}
          </div>

          {inspector && (
            <div className={styles.inspector}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-2)" }}>
                <strong style={{ fontSize: "var(--text-sm)", flex: 1 }}>{inspector.title}</strong>
                <Button onClick={() => setInspector(null)}>Close</Button>
              </div>
              <pre className={styles.inspectorText}>{inspector.text || "(empty)"}</pre>
            </div>
          )}

          {(data?.log_progress_active || data?.paused) && (
            <div className={styles.progress} role="status">
              {data?.paused ? (
                <>
                  <Tag tone="warn">paused</Tag>
                  <span>The agent will not continue until resumed.</span>
                </>
              ) : (
                <>
                  <span className={styles.pulse} />
                  <span>{data?.log_progress || "Working…"}</span>
                </>
              )}
              <div style={{ flex: 1 }} />
              <Button
                onClick={() => void act(() => setPaused(contextId, !data?.paused))}
                disabled={!contextId}
              >
                {data?.paused ? "Resume" : "Pause"}
              </Button>
              <Button
                onClick={() => void act(() => nudge(contextId))}
                disabled={!contextId || data?.paused}
                title="Interrupt the current step without ending the conversation"
              >
                Nudge
              </Button>
              <Button
                variant="danger"
                onClick={() => void act(() => stop(contextId))}
                disabled={!contextId}
              >
                Stop
              </Button>
            </div>
          )}

          {attachments.length > 0 && (
            <div className={styles.attachments}>
              {attachments.map((file, index) => (
                <span key={`${file.name}-${index}`} className={styles.attachment}>
                  <span className={styles.attachmentName}>{file.name}</span>
                  <span className={styles.attachmentSize}>{formatBytes(file.size)}</span>
                  <button
                    type="button"
                    className={styles.attachmentRemove}
                    aria-label={`Remove ${file.name}`}
                    onClick={() =>
                      setAttachments((current) => current.filter((_, i) => i !== index))
                    }
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className={styles.composer}>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="visually-hidden"
              aria-label="Attach files"
              onChange={(event) => {
                addFiles(event.target.files);
                // Reset so selecting the same file twice still fires a change.
                event.target.value = "";
              }}
            />
            <button
              type="button"
              className={styles.iconButton}
              onClick={() => fileInputRef.current?.click()}
              disabled={sending}
              title="Attach files"
              aria-label="Attach files"
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M21.44 11.05l-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a1.5 1.5 0 0 1-2.12-2.12l8.49-8.48" />
              </svg>
            </button>
            {/* Offered only where the browser implements it; see lib/dictation.ts. */}
            {dictation.supported && (
              <button
                type="button"
                className={
                  dictation.listening
                    ? `${styles.iconButton} ${styles.iconButtonRecording}`
                    : styles.iconButton
                }
                onClick={dictation.toggle}
                disabled={sending}
                aria-pressed={dictation.listening}
                title={dictation.listening ? DICTATION_STOP_LABEL : DICTATION_START_LABEL}
                aria-label={dictation.listening ? DICTATION_STOP_LABEL : DICTATION_START_LABEL}
              >
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z" />
                  <path d="M19 11a7 7 0 0 1-14 0" />
                  <path d="M12 18v3" />
                </svg>
              </button>
            )}
            <div className={styles.composerWrap} style={{ flex: 1 }}>
              {commandMatches.length > 0 && (
                <div className={styles.commandMenu} role="listbox" aria-label="Slash commands">
                  {commandMatches.map((command, index) => (
                    <button
                      key={command.name}
                      type="button"
                      role="option"
                      aria-selected={index === commandIndex}
                      className={
                        index === commandIndex
                          ? `${styles.commandItem} ${styles.commandItemActive}`
                          : styles.commandItem
                      }
                      onMouseEnter={() => setCommandIndex(index)}
                      onClick={() => applyCommand(command)}
                    >
                      <span className={styles.commandName}>/{command.name}</span>
                      <div className={styles.commandHint}>
                        {command.description || command.argument_hint || ""}
                      </div>
                    </button>
                  ))}
                </div>
              )}
              <textarea
                ref={composerRef}
                className={styles.composerInput}
                style={{ width: "100%" }}
                placeholder="Message the agent…  Type / for commands"
                value={draft}
                disabled={sending}
                aria-label="Message"
                onChange={(event) => {
                  setDraft(event.target.value);
                  setCommandIndex(0);
                }}
                onKeyDown={(event) => {
                  if (commandMatches.length > 0) {
                    if (event.key === "ArrowDown") {
                      event.preventDefault();
                      setCommandIndex((current) => (current + 1) % commandMatches.length);
                      return;
                    }
                    if (event.key === "ArrowUp") {
                      event.preventDefault();
                      setCommandIndex((current) =>
                        (current - 1 + commandMatches.length) % commandMatches.length,
                      );
                      return;
                    }
                    if (event.key === "Tab" || event.key === "Enter") {
                      const chosen = commandMatches[commandIndex];
                      if (chosen) {
                        event.preventDefault();
                        applyCommand(chosen);
                        return;
                      }
                    }
                    if (event.key === "Escape") {
                      event.preventDefault();
                      setDraft("");
                      return;
                    }
                  }
                  // Enter sends, Shift+Enter breaks the line.
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void onSend();
                  }
                }}
              />
            </div>
            {running && (
              <Button
                disabled={sending || !draft.trim()}
                onClick={() => void onQueue()}
                title="Send this after the agent finishes what it is doing"
              >
                Queue
              </Button>
            )}
            <Button
              variant="primary"
              disabled={sending || (!draft.trim() && attachments.length === 0)}
              onClick={() => void onSend()}
            >
              {sending ? "Sending…" : "Send"}
            </Button>
          </div>
          {queued > 0 && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
                padding: "var(--space-2) 0 0",
                fontSize: "var(--text-sm)",
                color: "var(--ink-3)",
              }}
            >
              <span>
                {queued} message{queued === 1 ? "" : "s"} queued.
              </span>
              <Button onClick={() => void onSendQueue()}>Send now</Button>
              <Button variant="ghost" onClick={() => void onClearQueue()}>
                Clear queue
              </Button>
            </div>
          )}
        </section>
      </div>

      {contextId && (
        <ConversationPanel contextId={contextId} />
      )}

      {contextId && (
        <div className={styles.footer}>
          <Tag tone="muted">
            <Mono>{contextId}</Mono>
          </Tag>
          <Tag tone={connection === "live" ? "ok" : connection === "connecting" ? "muted" : "warn"}>
            {connection === "live"
              ? "live"
              : connection === "connecting"
                ? "connecting"
                : "polling — live updates unavailable"}
          </Tag>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            className={styles.footerAction}
            title="Show the prompt the model actually receives"
            onClick={() =>
              void act(async () => {
                const window_ = await getContextWindow(contextId);
                setInspector({
                  title: `Context window — ${window_.tokens.toLocaleString()} tokens`,
                  text: window_.content,
                });
              })
            }
          >
            Inspect context
          </button>
          <button
            type="button"
            className={styles.footerAction}
            title="Summarise older turns to free context"
            onClick={() => void onCompact()}
          >
            Compact
          </button>
          <button
            type="button"
            className={styles.footerAction}
            title="Download the transcript"
            onClick={() =>
              void act(async () => {
                const result = await exportChat(contextId);
                const content = String(result.content ?? "");
                if (!content) throw new Error("Nothing to export.");
                // Built here rather than fetched: chat_export returns the
                // document in its JSON body, not as a file.
                const blob = new Blob([content], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement("a");
                anchor.href = url;
                anchor.download = `conversation-${contextId}.json`;
                document.body.appendChild(anchor);
                anchor.click();
                anchor.remove();
                window.setTimeout(() => URL.revokeObjectURL(url), 0);
                toast.success("Transcript downloaded.");
              })
            }
          >
            Export
          </button>
          <button
            type="button"
            className={styles.footerAction}
            title="Clear the transcript, keeping the conversation itself"
            onClick={() => void onReset()}
          >
            Reset
          </button>
          <button
            type="button"
            className={`${styles.footerAction} ${styles.footerActionDanger}`}
            onClick={() => void onDelete()}
          >
            Delete conversation
          </button>
        </div>
      )}
    </div>
  );
}
