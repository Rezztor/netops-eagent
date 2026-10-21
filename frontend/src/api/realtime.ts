/**
 * Live state over Socket.IO.
 *
 * The backend already pushes conversation state; the polling loop the first
 * chat pass used was a placeholder for this. The protocol, from
 * `extensions/python/webui_ws_event/_10_state_sync.py` and
 * `helpers/state_monitor.py`:
 *
 *   client -> `state_request` { context, log_from, notifications_from, timezone }
 *   server -> `state_push`    { runtime_epoch, seq, snapshot }
 *
 * `snapshot` is the same shape `/api/poll` returns, which is why this is a
 * transport change rather than a rewrite of everything downstream.
 *
 * **Pushes are incremental.** After each push the server advances the
 * connection's `log_from` to the snapshot's `log_version`, so the next push
 * carries only entries added since. A client that replaces its log on every
 * push instead of merging shows the last few lines of a conversation and loses
 * the rest — so merging is this module's responsibility, not each screen's.
 */

import { io, type Socket } from "socket.io-client";

import { getCsrfToken, refreshCsrfToken } from "./client";
import type { ChatContext, LogItem, Notification, PollResponse } from "./chat";

const NAMESPACE = "/ws";

// The server activates handlers named in the connection's auth payload
// (helpers/ws.py `_on_connect` reads `auth.handlers`). A connection that names
// none establishes normally and then routes nothing, which looks like a working
// socket that delivers no data.
//
// Both are declared up front rather than added later: handlers are bound at
// connect time, so activating one afterwards means reconnecting, and one socket
// carrying both is simpler than coordinating that.
const HANDLERS = ["ws_webui"];

export interface StateRequest {
  context: string;
  timezone: string;
}

export type ConnectionState = "connecting" | "live" | "offline";

/**
 * What arrives on the wire.
 *
 * The server wraps every emission in the same envelope the client sends -- see
 * `helpers/ws_manager.py`, which builds `{ts, correlationId, eventId,
 * handlerId, data}`. The state payload is inside `data`, so reading
 * `envelope.snapshot` finds nothing and the screen stays empty while the socket
 * looks perfectly healthy.
 */
interface StatePushEnvelope {
  ts?: string;
  correlationId?: string;
  eventId?: string;
  handlerId?: string;
  data?: {
    runtime_epoch?: string;
    seq?: number;
    snapshot?: Partial<PollResponse>;
  };
  /** Tolerated fallback if the manager's envelope ever stops wrapping. */
  snapshot?: Partial<PollResponse>;
}

export interface StateListener {
  onState: (state: PollResponse) => void;
  onConnectionChange: (state: ConnectionState) => void;
}

/**
 * Accumulates pushed snapshots into the current view of one conversation.
 *
 * Kept separate from the socket so the merge rules can be tested without a
 * server, and so a screen never has to know that pushes are incremental.
 */
export class StateAccumulator {
  private contextId = "";
  private logGuid = "";
  private logs: LogItem[] = [];
  private contexts: ChatContext[] = [];
  // Like the log, notifications arrive as what changed since the last push. Kept here and merged by id: taking
  // each push's list as the whole list emptied the bell on the next push, which carries none.
  private notifications: Notification[] | null = null;
  private notificationsGuid = "";
  private latest: PollResponse | null = null;

  /** Switching conversation invalidates the accumulated log. */
  reset(contextId: string): void {
    this.contextId = contextId;
    this.logGuid = "";
    this.logs = [];
    this.contexts = [];
    // The request that follows asks for notifications from the start, so the list is rebuilt.
    this.notifications = null;
    this.notificationsGuid = "";
    this.latest = null;
  }

  apply(snapshot: Partial<PollResponse>): PollResponse {
    // A push for a conversation we are no longer showing must not merge into
    // the one we are.
    const pushedContext = typeof snapshot.context === "string" ? snapshot.context : "";
    if (pushedContext && this.contextId && pushedContext !== this.contextId) {
      return this.current();
    }

    // A new guid is a different log -- the conversation was cleared or reloaded.
    // Merging across it keeps the old transcript, and splices it into the new one
    // by entry number; the push after a clear carries no entries to replace it.
    const guid = typeof snapshot.log_guid === "string" ? snapshot.log_guid : "";
    if (guid && this.logGuid && guid !== this.logGuid) this.logs = [];
    if (guid) this.logGuid = guid;

    if (Array.isArray(snapshot.contexts)) {
      this.contexts = snapshot.contexts;
    }

    if (Array.isArray(snapshot.logs) && snapshot.logs.length > 0) {
      this.logs = mergeLogs(this.logs, snapshot.logs);
    }

    if (Array.isArray(snapshot.notifications)) {
      // A new guid is a different list -- it was cleared.
      const listGuid = typeof snapshot.notifications_guid === "string" ? snapshot.notifications_guid : "";
      if (listGuid && this.notificationsGuid && listGuid !== this.notificationsGuid) this.notifications = [];
      if (listGuid) this.notificationsGuid = listGuid;
      const byId = new Map<string, Notification>();
      for (const item of this.notifications ?? []) byId.set(item.id, item);
      for (const item of snapshot.notifications) byId.set(item.id, item);
      this.notifications = Array.from(byId.values()).sort((a, b) => a.no - b.no);
    }

    this.latest = {
      context: pushedContext || this.contextId,
      contexts: this.contexts,
      logs: this.logs,
      log_guid: snapshot.log_guid ?? this.latest?.log_guid ?? "",
      log_version: snapshot.log_version ?? this.latest?.log_version ?? 0,
      log_progress: snapshot.log_progress ?? "",
      log_progress_active: Boolean(snapshot.log_progress_active),
      paused: Boolean(snapshot.paused ?? this.latest?.paused),
      ...(snapshot.tasks !== undefined ? { tasks: snapshot.tasks } : {}),
      ...(this.notifications !== null ? { notifications: this.notifications } : {}),
    };
    return this.latest;
  }

  current(): PollResponse {
    return (
      this.latest ?? {
        context: this.contextId,
        contexts: this.contexts,
        logs: this.logs,
        log_guid: "",
        log_version: 0,
        log_progress: "",
        log_progress_active: false,
        paused: false,
      }
    );
  }
}

/**
 * Merge a batch of pushed entries into the log.
 *
 * Entries are keyed by `no`. An entry that already exists is replaced rather
 * than appended, because the backend revises entries in place while a tool runs
 * — that is how streaming output grows. Appending would show the same message
 * repeatedly, once per token batch.
 */
/**
 * Pull the snapshot out of a `state_push` envelope.
 *
 * Tolerates an unwrapped payload as well: the envelope is the manager's
 * contract rather than the state monitor's, and one changing without the other
 * should degrade to still working.
 */
/**
 * Pull the payload out of the manager's envelope.
 *
 * Everything the server sends is wrapped as `{ts, correlationId, eventId,
 * handlerId, data}`. Reading the payload directly finds nothing while the socket
 * looks perfectly healthy, which is how this was shipped the first time.
 */
export function unwrapEnvelope(envelope: unknown): unknown {
  if (!envelope || typeof envelope !== "object") return envelope;
  const inner = (envelope as { data?: unknown }).data;
  return inner !== undefined ? inner : envelope;
}

export function unwrapSnapshot(
  envelope: StatePushEnvelope | null | undefined,
): Partial<PollResponse> | null {
  if (!envelope || typeof envelope !== "object") return null;
  const inner = envelope.data;
  if (inner && typeof inner === "object" && inner.snapshot) return inner.snapshot;
  return envelope.snapshot ?? null;
}

export function mergeLogs(existing: LogItem[], incoming: LogItem[]): LogItem[] {
  const byNo = new Map<number, LogItem>();
  for (const item of existing) byNo.set(item.no, item);
  for (const item of incoming) byNo.set(item.no, item);
  return Array.from(byNo.values()).sort((a, b) => a.no - b.no);
}

/**
 * A snapshot as the screen may show it for the conversation it has selected.
 *
 * Between choosing a conversation and its first push, the held snapshot is still
 * the previous one. Showing its transcript under the new id lets a card from the
 * old conversation answer into the new one, so its entries and progress are
 * withheld; the conversation list stays, as it is not per-conversation.
 */
export function forContext(state: PollResponse | null, contextId: string): PollResponse | null {
  if (!state || !contextId || !state.context || state.context === contextId) return state;
  return { ...state, logs: [], log_progress: "", log_progress_active: false, paused: false };
}

/**
 * Prefer the fresher of a live push and a poll fallback.
 *
 * While the socket is live the push wins. Once it drops, a newer poll must be
 * shown or the transcript freezes at the last push.
 */
export function pickChatState(
  live: PollResponse | null,
  fallback: PollResponse | null,
  connection: ConnectionState,
): PollResponse | null {
  if (connection === "live") return live ?? fallback;
  if (!live) return fallback;
  if (!fallback) return live;
  return (fallback.log_version ?? 0) >= (live.log_version ?? 0) ? fallback : live;
}

/**
 * True when the server connected us but refused every handler — typically a
 * stale CSRF token after a process restart, which otherwise looks "live" and
 * delivers nothing.
 */
export function isSocketHandlerRefusal(reply: unknown): boolean {
  const payload = unwrapEnvelope(reply);
  if (!payload || typeof payload !== "object") return false;
  const results = (payload as { results?: unknown }).results;
  if (!Array.isArray(results)) return false;
  return results.some((entry) => {
    if (!entry || typeof entry !== "object") return false;
    const code = (entry as { error?: { code?: string } }).error?.code;
    return (
      code === "NO_HANDLERS" ||
      code === "CSRF_INVALID" ||
      code === "CSRF_COOKIE" ||
      code === "CSRF_MISSING"
    );
  });
}

/**
 * One live connection.
 *
 * Socket.IO reconnects on its own; what this adds is re-sending `state_request`
 * afterwards, because the server keeps the projection per connection and a new
 * connection has none. Without that a reconnect looks connected and silently
 * delivers nothing.
 */
export class RealtimeState {
  private socket: Socket | null = null;
  private stateRequest: StateRequest | null = null;
  private listener: StateListener | null = null;
  private readonly accumulator = new StateAccumulator();
  /** One recovery attempt per refusal streak; a push clears it. */
  private csrfRecoveryAttempts = 0;

  connect(request: StateRequest, listener: StateListener): void {
    this.stateRequest = request;
    this.listener = listener;
    this.accumulator.reset(request.context);

    if (!this.socket) {
      // Same origin, so no URL: the page and the socket share a host by
      // construction, which is what makes the session cookie apply.
      this.socket = io(NAMESPACE, {
        transports: ["websocket", "polling"],
        withCredentials: true,
        // The socket is origin-checked server-side as well as cookie-gated, so
        // it only works same-origin -- which is the arrangement anyway.
        // A function so each reconnect reads the current token: a static object
        // keeps the token from the first open across a server restart.
        auth: (cb) => {
          cb({ csrf_token: getCsrfToken() ?? "", handlers: HANDLERS });
        },
      });

      this.socket.on("connect", () => {
        listener.onConnectionChange("live");
        this.sendStateRequest();
      });
      this.socket.on("disconnect", () => listener.onConnectionChange("offline"));
      this.socket.on("connect_error", () => listener.onConnectionChange("offline"));
      this.socket.on("state_push", (envelope: StatePushEnvelope) => {
        const snapshot = unwrapSnapshot(envelope);
        if (!snapshot) return;
        this.csrfRecoveryAttempts = 0;
        listener.onState(this.accumulator.apply(snapshot));
      });
    }

    listener.onConnectionChange(this.socket.connected ? "live" : "connecting");
    if (this.socket.connected) this.sendStateRequest();
  }

  /** Point the connection at a different conversation. */
  setContext(contextId: string): void {
    if (!this.stateRequest || this.stateRequest.context === contextId) return;
    this.stateRequest = { ...this.stateRequest, context: contextId };
    this.accumulator.reset(contextId);
    this.sendStateRequest();
  }

  private sendStateRequest(): void {
    if (!this.socket?.connected || !this.stateRequest) return;
    // Every client event is wrapped: the server reads `incoming["data"]`
    // (helpers/ws_manager.py) and ignores a flat payload entirely, which
    // presents as a connection that establishes and then delivers nothing.
    this.socket.emit(
      "state_request",
      {
        ts: new Date().toISOString(),
        correlationId: `rt-${Date.now()}`,
        data: {
          context: this.stateRequest.context,
          log_from: 0,
          notifications_from: 0,
          timezone: this.stateRequest.timezone,
        },
      },
      (reply: unknown) => {
        // Handler activation can fail CSRF while the socket still connects —
        // the page says "live" and then receives nothing unless this is read.
        if (!isSocketHandlerRefusal(reply)) {
          this.csrfRecoveryAttempts = 0;
          return;
        }
        void this.recoverFromHandlerRefusal();
      },
    );
  }

  private async recoverFromHandlerRefusal(): Promise<void> {
    const listener = this.listener;
    if (!listener || !this.socket) return;
    // Stop claiming live while we have no handlers.
    listener.onConnectionChange("offline");
    if (this.csrfRecoveryAttempts >= 1) return;
    this.csrfRecoveryAttempts += 1;
    const token = await refreshCsrfToken();
    if (!token || !this.socket) return;
    // disconnect/connect re-runs auth(), which reads the refreshed token.
    this.socket.disconnect();
    this.socket.connect();
  }

  /**
   * Send an event on the shared connection.
   *
   * Wrapped in the envelope the server unwraps -- see `sendStateRequest`. Used
   * by surfaces other than state synchronisation, which have their own handler
   * activated on the same socket.
   */
  emit(event: string, payload: Record<string, unknown>): void {
    if (!this.socket?.connected) return;
    this.socket.emit(event, {
      ts: new Date().toISOString(),
      correlationId: `rt-${Date.now()}`,
      data: payload,
    });
  }

  /**
   * Send an event and wait for the server's reply.
   *
   * Socket.IO acknowledgements carry the same envelope as a push, so the reply
   * is unwrapped the same way.
   */
  request<T>(event: string, payload: Record<string, unknown>, timeoutMs = 20000): Promise<T> {
    return new Promise((resolve, reject) => {
      if (!this.socket?.connected) {
        reject(new Error("Not connected."));
        return;
      }
      const timer = window.setTimeout(
        () => reject(new Error(`${event} timed out.`)),
        timeoutMs,
      );
      this.socket.emit(
        event,
        {
          ts: new Date().toISOString(),
          correlationId: `rt-${Date.now()}`,
          data: payload,
        },
        (reply: unknown) => {
          window.clearTimeout(timer);
          resolve(unwrapEnvelope(reply) as T);
        },
      );
    });
  }

  /** Subscribe to a pushed event. Returns an unsubscribe function. */
  on(event: string, handler: (payload: unknown) => void): () => void {
    const socket = this.socket;
    if (!socket) return () => {};
    const wrapped = (envelope: unknown) => handler(unwrapEnvelope(envelope));
    socket.on(event, wrapped);
    return () => {
      socket.off(event, wrapped);
    };
  }

  isConnected(): boolean {
    return Boolean(this.socket?.connected);
  }

  disconnect(): void {
    this.socket?.disconnect();
    this.socket = null;
    this.stateRequest = null;
  }
}
