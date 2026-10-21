/**
 * The single way this app talks to the backend.
 *
 * Auth is a Flask session cookie plus a CSRF token, and the backend requires the
 * token on *every* authenticated endpoint including GETs -- `csrf_protect` in
 * helpers/api.py does not exempt safe methods. So every call here sends it, and
 * nothing bypasses this module to call `fetch` directly.
 *
 * Requests are same-origin and rely on the cookie the browser already holds;
 * `credentials: "same-origin"` is explicit rather than assumed. In development
 * the Vite proxy makes the backend look same-origin for exactly this reason.
 */

import type { BootstrapResponse, Session } from "./types";

/** Thrown for any non-2xx response, carrying enough to decide what to do next. */
export class ApiError extends Error {
  readonly status: number;
  readonly endpoint: string;
  readonly body: unknown;

  constructor(endpoint: string, status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.endpoint = endpoint;
    this.status = status;
    this.body = body;
  }

  /** The session is gone or was never established. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** Authenticated, but the role does not carry the required permission. */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  /**
   * What the server actually said, when `message` had to replace it with a
   * plain sentence: a plain-text 500 body or a permission code. For a folded
   * "Technical details", never the headline.
   */
  get detail(): string {
    if (typeof this.body === "string") return this.body.trim();
    const error = this.body && typeof this.body === "object" ? (this.body as { error?: unknown }).error : null;
    return typeof error === "string" && error.trim() !== this.message ? error.trim() : "";
  }
}

/** The sentence for a 403 that names a permission. The code means nothing to the person refused. */
export const NO_ACCESS_MESSAGE = "You don't have access to this. Ask an administrator if you need it.";

let csrfToken: string | null = null;
let runtimeId: string | null = null;

/**
 * Publish the CSRF token as a cookie named for the runtime.
 *
 * HTTP does not read it: `csrf_protect` checks the header only, because a
 * browser attaches a cookie to a forged request too. The socket does:
 * `_check_security` in
 * helpers/ws.py requires the auth token, the session token and this cookie to
 * agree, all three. A frontend that only sends the header connects to the socket
 * and is then silently refused its handler -- which presents as a live
 * connection that delivers nothing at all.
 *
 * Written from script rather than set by the server because the name embeds the
 * runtime id, which changes per process.
 */
function publishCsrfCookie(token: string, runtime: string): void {
  // Guarded: this module is also imported by tests and could be imported by a
  // future server-side render, neither of which has a document.
  if (typeof document === "undefined" || typeof window === "undefined") return;
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `csrf_token_${runtime}=${token}; SameSite=Lax; Path=/${secure}`;
}

/**
 * Seed the CSRF token from the bootstrap payload.
 *
 * Bootstrap returns the same token `GET /api/csrf_token` would, so a cold start
 * needs one request rather than two.
 */
export function setCsrfToken(token: string | null, runtime?: string | null): void {
  csrfToken = token;
  if (runtime !== undefined) runtimeId = runtime;
  if (token && runtimeId) publishCsrfCookie(token, runtimeId);
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

/** Fetch a token directly. Used to recover from a rotated session or runtime. */
export async function refreshCsrfToken(): Promise<string | null> {
  const response = await fetch("/api/csrf_token", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) return null;
  const data = (await response.json()) as { token?: string; runtime_id?: string };
  setCsrfToken(data.token ?? null, data.runtime_id ?? runtimeId);
  return csrfToken;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** Internal: prevents the 403 CSRF retry from looping. */
  retried?: boolean;
}

async function readBody(response: Response): Promise<unknown> {
  // Unhandled backend errors come back as text/plain, not JSON, so this cannot
  // assume a JSON body even on failure.
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/**
 * One sentence a person can act on.
 *
 * A JSON `error` is a sentence a handler wrote for the reader and is used as
 * it is, except a permission refusal, which names a code. A plain-text body is
 * whatever an unexpected exception said -- Werkzeug's "415 Unsupported Media
 * Type: Did not attempt to load JSON...", a path, a slot -- so it is replaced
 * and kept on `ApiError.detail` instead.
 */
function messageFor(status: number, body: unknown): string {
  if (body && typeof body === "object" && "error" in body) {
    const error = (body as { error?: unknown }).error;
    if (typeof error === "string" && error.trim()) {
      if (status === 403 && /required permission/i.test(error)) return NO_ACCESS_MESSAGE;
      return error.trim();
    }
  }
  if (status === 403) return NO_ACCESS_MESSAGE;
  return `Something went wrong on the server (HTTP ${status}). Try again; if it repeats, tell your administrator when it happened.`;
}

/**
 * Call an API endpoint.
 *
 * `endpoint` is the dispatch path without the `/api/` prefix, matching how
 * `register_api_route` resolves handlers: "whoami", "users_list",
 * "plugins/_memory/dashboard".
 */
export async function apiRequest<T>(
  endpoint: string,
  options: RequestOptions = {},
): Promise<T> {
  const method = options.method ?? "POST";
  const headers: Record<string, string> = { Accept: "application/json" };

  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let payload: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(options.body);
  } else if (method !== "GET") {
    // Handlers read `request.get_json()` and fall back to {} on an empty body,
    // so an explicit empty object keeps the request well-formed.
    headers["Content-Type"] = "application/json";
    payload = "{}";
  }

  const init: RequestInit = {
    method,
    headers,
    credentials: "same-origin",
  };
  if (payload !== undefined) init.body = payload;
  if (options.signal) init.signal = options.signal;

  const response = await fetch(`/api/${endpoint}`, init);

  if (response.ok) {
    return (await readBody(response)) as T;
  }

  const body = await readBody(response);

  // A 403 can mean two different things: a stale CSRF token, or a genuine
  // permission denial. Only the first is worth retrying, and only once.
  if (response.status === 403 && !options.retried && isCsrfFailure(body)) {
    const token = await refreshCsrfToken();
    if (token) {
      return apiRequest<T>(endpoint, { ...options, retried: true });
    }
  }

  // The session ended under an open page (expired, password changed elsewhere, account disabled). Nothing on the
  // page works again until the user signs in, so take them there; they come back to where they were.
  if (response.status === 401) redirectToLogin();

  throw new ApiError(endpoint, response.status, messageFor(response.status, body), body);
}

/**
 * The failure message in a 200 response, or null if there is none.
 *
 * These endpoints answer failure with **HTTP 200 and a body that says so** --
 * `{"ok": false, "error": ...}` from the account handlers, `{"error": ...}` from
 * the work-directory ones. `apiRequest` returns both without complaint, so
 * `await action(); toast.success(...)` toasted on failure: creating an account
 * with a short password said "admin created" and created nothing; creating a
 * folder at the root said "Folder created." and created nothing. Found in a GUI
 * QA pass on 2026-09-19.
 *
 * Deliberately narrow, because an `error` field does not always mean failure.
 * `model_search` answers `{models: [...], error: "..."}` -- a registry fallback
 * plus the reason the live lookup failed -- and `/api/health` answers
 * `{gitinfo, error: null}`. Treating either as a failure would break a working
 * screen to fix a lying one. So: an explicit `ok: false` is a failure, and a
 * lone `error` string is a failure only when nothing else in the body carries a
 * result.
 */
export function failureMessageIn(body: unknown): string | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const record = body as Record<string, unknown>;
  const stated = typeof record["error"] === "string" ? record["error"].trim() : "";

  if (record["ok"] === false) return stated || "The server rejected that.";

  // `{"error": "..."}` with no `ok` and no payload beside it: the shape the
  // work-directory handlers use. `data` is what they return on success.
  if (stated && record["ok"] === undefined && !("data" in record)) {
    const others = Object.keys(record).filter((key) => key !== "error");
    if (others.length === 0) return stated;
  }
  return null;
}

/**
 * For a call whose result is an outcome rather than data.
 *
 * Throws when the body says the thing did not happen, so the `try/catch`
 * around it reports the truth. Read paths keep using `apiRequest`: several
 * screens render `ok: false` inline -- the approval queue, SIEM forwarding, the
 * device inventory -- and making every call throw would turn those explanations
 * into exceptions.
 */
export async function apiAction<T>(
  endpoint: string,
  options: RequestOptions = {},
): Promise<T> {
  const body = await apiRequest<T>(endpoint, options);
  const failure = failureMessageIn(body);
  if (failure) throw new ApiError(endpoint, 200, failure, body);
  return body;
}

function isCsrfFailure(body: unknown): boolean {
  return typeof body === "string" && body.toLowerCase().includes("csrf");
}

/**
 * Send a multipart request.
 *
 * Separate from `apiRequest` because the two cannot share a code path: the
 * browser must set `Content-Type` itself so it can append the multipart
 * boundary, and setting it by hand produces a body the server cannot parse. The
 * CSRF header still applies.
 */
export async function apiUpload<T>(
  endpoint: string,
  form: FormData,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

  const init: RequestInit = {
    method: "POST",
    headers,
    body: form,
    credentials: "same-origin",
  };
  if (options.signal) init.signal = options.signal;

  const response = await fetch(`/api/${endpoint}`, init);
  const body = await readBody(response);
  if (!response.ok) {
    throw new ApiError(endpoint, response.status, messageFor(response.status, body), body);
  }
  return body as T;
}

/**
 * Fetch a binary response and hand it to the browser as a download.
 *
 * A plain `<a download>` cannot carry the CSRF header, and the endpoints require
 * it. Fetching the bytes and saving a blob also means a failure surfaces as an
 * error rather than as a downloaded file containing an error page.
 */
export async function apiDownload(
  endpoint: string,
  options: { method?: "GET" | "POST"; body?: unknown; filename: string },
): Promise<void> {
  const headers: Record<string, string> = {};
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

  const init: RequestInit = {
    method: options.method ?? "GET",
    headers,
    credentials: "same-origin",
  };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }

  const response = await fetch(`/api/${endpoint}`, init);
  if (!response.ok) {
    const body = await readBody(response);
    throw new ApiError(endpoint, response.status, messageFor(response.status, body), body);
  }

  const blob = await response.blob();
  // A handler that refuses answers 200 with {ok: false, error}; saving that as the file would hide the refusal.
  if (blob.type.includes("application/json")) {
    try {
      const body = JSON.parse(await blob.text()) as { ok?: unknown };
      if (body && body.ok === false) throw new ApiError(endpoint, response.status, messageFor(response.status, body), body);
    } catch (problem) {
      if (problem instanceof ApiError) throw problem;
    }
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = options.filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoked on the next tick: revoking synchronously can cancel the download in
  // some browsers before they have read the blob.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Fetch a binary response as an object URL, for display rather than download.
 *
 * The caller owns the returned URL and must revoke it, or the blob is retained
 * for the life of the page.
 */
export async function apiObjectUrl(endpoint: string): Promise<string> {
  const headers: Record<string, string> = {};
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

  const response = await fetch(`/api/${endpoint}`, {
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    const body = await readBody(response);
    throw new ApiError(endpoint, response.status, messageFor(response.status, body), body);
  }
  return URL.createObjectURL(await response.blob());
}

/**
 * Load the startup payload.
 *
 * Deliberately does not go through `apiRequest`: it runs before a CSRF token
 * exists, and the endpoint is the one place that does not require one.
 */
export async function loadSession(signal?: AbortSignal): Promise<Session> {
  const init: RequestInit = {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  };
  if (signal) init.signal = signal;

  const response = await fetch("/api/bootstrap", init);
  if (!response.ok) {
    const body = await readBody(response);
    throw new ApiError(
      "bootstrap",
      response.status,
      messageFor(response.status, body),
      body,
    );
  }

  const data = (await response.json()) as BootstrapResponse;

  if (!data.authenticated) {
    setCsrfToken(null);
    return { authenticated: false, version: data.version };
  }

  // The backend guarantees these once authenticated is true. If any are missing
  // the payload is malformed, and failing loudly here beats rendering an app
  // with a half-built session.
  if (!data.user || !data.permissions || !data.runtime || !data.preferences) {
    throw new ApiError("bootstrap", 200, "Incomplete bootstrap payload", data);
  }

  setCsrfToken(data.csrf_token ?? null, data.runtime?.runtime_id ?? null);

  return {
    authenticated: true,
    version: data.version,
    user: data.user,
    permissions: data.permissions,
    runtime: data.runtime,
    csrfToken: data.csrf_token ?? "",
    preferences: data.preferences,
    webuiExtensions: data.webui_extensions ?? null,
    licence: data.licence ?? null,
    update: data.update ?? null,
    updatesAvailable: data.updates_available ?? null,
  };
}

/**
 * Sign out: a POST with the CSRF token, then the sign-in page whether or not it
 * succeeded -- a session that is already gone has nothing left to end. Never a
 * GET, which any other site can make the browser send.
 */
export function signOut(): void {
  void apiRequest("logout")
    .catch(() => undefined)
    .finally(() => {
      window.location.href = "/login";
    });
}

/** Send the browser to the backend-rendered login page. */
export function redirectToLogin(): void {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.assign(`/login?next=${next}`);
}
