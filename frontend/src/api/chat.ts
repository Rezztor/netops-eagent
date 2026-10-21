/**
 * Chat endpoints.
 *
 * The backend is poll-based here rather than push: `/api/poll` returns the
 * conversation list and a window of log entries, and the caller tracks
 * `log_version` to know whether anything changed. There is a Socket.IO channel
 * as well, but polling is the contract that is stable today and it is what the
 * inherited interface actually runs on, so the port starts there. Streaming is a
 * later change to this one module, not to every screen.
 */

import { apiRequest } from "./client";

export interface ChatContext {
  id: string;
  name?: string;
  created_at?: string;
  no?: number;
  last_message?: string;
  paused?: boolean;
  /** The agent profile this conversation runs under, e.g. "primary". */
  agent_profile?: string;
  /** Its display title, when the profile declares one. */
  agent_profile_label?: string;
  [key: string]: unknown;
}

/** One rendered line of the conversation. Shapes vary by `type`. */
export interface LogItem {
  no: number;
  id?: string;
  type: string;
  heading?: string;
  content?: string;
  kvps?: Record<string, unknown> | null;
  timestamp?: string;
  agentno?: number;
}

export interface PollResponse {
  context: string;
  contexts: ChatContext[];
  logs: LogItem[];
  log_guid: string;
  log_version: number;
  log_progress: string;
  log_progress_active: boolean;
  /** Approximate tokens in the prompt this conversation last sent. 0 before the
   *  first turn, and after compaction clears the stored window. */
  context_tokens?: number;
  paused: boolean;
  tasks?: unknown[];
  notifications?: Notification[];
  /** Changes when the list is cleared: what was held belongs to the old list. */
  notifications_guid?: string;
}

export function poll(params: {
  context?: string;
  logFrom?: number;
  timezone?: string;
}): Promise<PollResponse> {
  return apiRequest<PollResponse>("poll", {
    body: {
      context: params.context ?? "",
      log_from: params.logFrom ?? 0,
      notifications_from: 0,
      timezone: params.timezone ?? "UTC",
    },
  });
}

/**
 * Send a message without waiting for the answer.
 *
 * `message` blocks until the agent finishes, which for a long task means a
 * request held open for minutes. `message_async` returns as soon as the work is
 * queued and the reply arrives through polling.
 */
export function sendMessage(text: string, context: string, answers?: string) {
  return apiRequest<{ message: string; context: string }>("message_async", {
    // `answers` is the interaction card this message replies to, when it was
    // sent by clicking one. It ties the answer to the question in the record;
    // it is not an instruction, and the agent is not steered by it.
    body: { text, context, ...(answers ? { answers } : {}) },
  });
}

/**
 * Create a conversation.
 *
 * Returns `ctxid`, not `context` -- unlike `message_async`, which returns
 * `context`. Normalised here so callers are not exposed to the difference.
 */
export async function createChat(currentContext = ""): Promise<{ context: string }> {
  const result = await apiRequest<{ ok?: boolean; ctxid?: string }>("chat_create", {
    body: { current_context: currentContext },
  });
  if (!result.ctxid) {
    throw new Error("chat_create returned no conversation id");
  }
  return { context: result.ctxid };
}

export function removeChat(context: string) {
  return apiRequest<{ ok?: boolean }>("chat_remove", { body: { context } });
}

export interface RemoveChatsResult {
  removed: string[];
  failed: { id: string; error: string }[];
  subagents_removed: number;
}

/** Delete several conversations in one request. Failures are per-id, not fatal. */
export function removeChats(contexts: string[], includeSubagents: boolean) {
  return apiRequest<RemoveChatsResult>("chat_remove_many", {
    body: { contexts, include_subagents: includeSubagents },
  });
}

export function resetChat(context: string) {
  return apiRequest<{ ok?: boolean }>("chat_reset", { body: { context } });
}

/* ---------- run controls ---------- */

/** Pause or resume the agent's loop for one conversation. */
export function setPaused(context: string, paused: boolean) {
  return apiRequest<{ ok?: boolean; paused?: boolean }>("pause", {
    body: { context, paused },
  });
}

/**
 * Interrupt the current step without ending the conversation.
 *
 * Note the parameter name: `nudge` reads `ctxid`, not `context`, unlike every
 * neighbouring endpoint. Sending `context` here silently nudges nothing.
 */
export function nudge(context: string) {
  return apiRequest<{ ok?: boolean }>("nudge", { body: { ctxid: context } });
}

/** Stop the agent's current work. */
export function stop(context: string) {
  return apiRequest<{ ok?: boolean }>("stop", { body: { context } });
}

/* ---------- message queue ---------- */

export interface QueuedMessage {
  id?: string;
  text?: string;
  attachments?: string[];
  [key: string]: unknown;
}

/** Queue a message to be sent when the agent is next free. */
export function queueAdd(context: string, text: string, attachments: string[] = []) {
  return apiRequest<{ ok?: boolean }>("message_queue_add", {
    body: { context, text, attachments },
  });
}

/** Remove one queued message, or all of them when `itemId` is omitted. */
export function queueRemove(context: string, itemId?: string) {
  return apiRequest<{ ok?: boolean }>("message_queue_remove", {
    body: itemId ? { context, item_id: itemId } : { context },
  });
}

/** Send the queue now rather than waiting for the agent to become free. */
export function queueSend(context: string) {
  return apiRequest<{ ok?: boolean }>("message_queue_send", { body: { context } });
}

/* ---------- attachments ---------- */

/**
 * Send a message with files attached.
 *
 * Multipart rather than JSON, because `api/message.py` only reads attachments
 * from a multipart body — its JSON branch sets `attachment_paths = []` and
 * silently drops them. A JSON send with an `attachments` field looks like it
 * worked and arrives without them.
 */
export async function sendMessageWithAttachments(
  text: string,
  context: string,
  attachments: File[],
): Promise<{ message: string; context: string }> {
  const { apiUpload } = await import("./client");
  const form = new FormData();
  form.append("text", text);
  form.append("context", context);
  for (const file of attachments) form.append("attachments", file);
  return apiUpload<{ message: string; context: string }>("message_async", form);
}

/* ---------- conversation history ---------- */

export interface ChatSummary {
  id: string;
  name?: string;
  created_at?: string;
  [key: string]: unknown;
}

/** Import previously exported conversations. */
export function loadChats(chats: string[]) {
  return apiRequest<{ ok?: boolean; ctxids?: string[] }>("chat_load", { body: { chats } });
}

/** Export one conversation as a transferable document. */
export function exportChat(context: string) {
  return apiRequest<{ ok?: boolean; content?: string }>("chat_export", {
    body: { ctxid: context },
  });
}

/* ---------- notifications ---------- */

export interface Notification {
  no: number;
  id: string;
  type?: string;
  priority?: string;
  title?: string;
  message?: string;
  /** HTML, expandable in the old interface. Rendered as text here. */
  detail?: string;
  timestamp?: string;
  display_time?: string;
  read?: boolean;
  group?: string;
  /** A path inside the application the item points at (`/approvals?change=...`), or "". */
  link?: string;
}

/**
 * Mark notifications read.
 *
 * The endpoint takes ids or a mark-all flag, not a cursor -- passing an empty
 * body marks nothing and reports success, which is a quiet way to build a badge
 * that never clears.
 */
export function markNotificationsRead(ids: string[]) {
  return apiRequest<{ success?: boolean; error?: string }>("notifications_mark_read", {
    body: { notification_ids: ids },
  });
}

export function markAllNotificationsRead() {
  return apiRequest<{ success?: boolean }>("notifications_mark_read", {
    body: { mark_all: true },
  });
}

export function clearNotifications() {
  return apiRequest<{ success?: boolean }>("notifications_clear");
}

/* ---------- inspection ---------- */

/**
 * The prompt as the model will actually see it, and its token count.
 *
 * Worth having in front of an administrator: the difference between what a user
 * typed and what the model receives is where most "why did it do that" questions
 * are answered.
 */
export function getContextWindow(context: string) {
  return apiRequest<{ content: string; tokens: number }>("ctx_window_get", {
    body: { context },
  });
}

export function getHistory(context: string) {
  return apiRequest<{ history?: unknown; tokens?: number }>("history_get", {
    body: { context },
  });
}

/* ---------- compaction and branching ---------- */

export interface CompactionStats {
  [key: string]: unknown;
}

export function getCompactionStats(context: string) {
  return apiRequest<{ ok?: boolean; stats?: CompactionStats; error?: string }>(
    "plugins/_chat_compaction/compact_chat",
    { body: { context, action: "stats" } },
  );
}

/**
 * Summarise older turns to free context. Runs in the background.
 *
 * Returns `ok: false` with a message rather than an error when there is not
 * enough content to compact -- a legitimate answer, not a failure. A caller that
 * only checks `error` reports success while nothing happened, so the message is
 * raised here instead.
 */
export async function compactChat(context: string, useChatModel = true): Promise<string> {
  const result = await apiRequest<{ ok?: boolean; message?: string; error?: string }>(
    "plugins/_chat_compaction/compact_chat",
    { body: { context, action: "compact", use_chat_model: useChatModel } },
  );
  if (result.error) throw new Error(result.error);
  if (result.ok === false) {
    throw new Error(result.message || "There is not enough in this conversation to compact.");
  }
  return result.message || "Compaction started.";
}

/**
 * Fork a conversation at one entry.
 *
 * Keyed on the log entry number, so the caller must pass the `no` from the
 * transcript rather than an index into its own array — the two diverge as soon
 * as anything is filtered.
 */
export function branchChat(context: string, logNo: number) {
  return apiRequest<{ ok?: boolean; ctxid?: string; error?: string }>(
    "plugins/_chat_branching/branch_chat",
    { body: { context, log_no: logNo } },
  );
}

/* ---------- slash commands ---------- */

export interface SlashCommand {
  name: string;
  description?: string;
  argument_hint?: string;
  [key: string]: unknown;
}

/** Commands available in this conversation's scope. */
export async function listCommands(contextId = ""): Promise<SlashCommand[]> {
  const result = await apiRequest<{ ok?: boolean; commands?: SlashCommand[]; error?: string }>(
    "plugins/_commands/commands",
    { body: { action: "list_effective", context_id: contextId } },
  );
  if (result.error) throw new Error(result.error);
  return result.commands ?? [];
}

/** A conversation that mentions the search term, and where. */
export interface ChatSearchHit {
  id: string;
  name: string;
  hits: number;
  snippet: string;
  last_message?: string;
}

/**
 * Search what was said across your own conversations.
 *
 * Only the transcript is searched, never the serialised system prompt that also
 * lives in a chat file — otherwise every conversation matches the system manual
 * and the feature is worse than useless.
 */
export async function searchChats(query: string, limit?: number) {
  return apiRequest<{ ok: boolean; query: string; chats?: ChatSearchHit[]; scanned?: number }>(
    "chats_search",
    { body: { query, ...(limit ? { limit } : {}) } },
  );
}
