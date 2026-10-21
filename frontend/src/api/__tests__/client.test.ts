/**
 * Tests for the API client.
 *
 * This is the module most able to break silently: a missing CSRF header does
 * not fail at build time, it fails as a 403 the first time someone tries to save
 * something. So the contract with the backend is pinned here.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiAction,
  apiDownload,
  apiRequest,
  failureMessageIn,
  getCsrfToken,
  loadSession,
  NO_ACCESS_MESSAGE,
  setCsrfToken,
} from "../client";

type FetchArgs = [input: string, init: RequestInit | undefined];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function textResponse(body: string, status: number): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain" } });
}

let calls: FetchArgs[] = [];

beforeEach(() => {
  calls = [];
  setCsrfToken(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** These tests run without a browser; a 401 sends the page to sign in, so it needs a page to send. */
function stubPage(pathname: string, search = "") {
  const assign = vi.fn();
  vi.stubGlobal("window", { location: { pathname, search, assign } });
  return assign;
}

function stubFetch(handler: (url: string, init?: RequestInit) => Response) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push([url, init]);
    return Promise.resolve(handler(url, init));
  });
}

describe("apiRequest", () => {
  it("prefixes the endpoint with /api/ to match backend dispatch", async () => {
    stubFetch(() => jsonResponse({ ok: true }));
    await apiRequest("users_list");
    expect(calls[0]?.[0]).toBe("/api/users_list");
  });

  it("sends the CSRF token on every call, including GETs", async () => {
    // csrf_protect in helpers/api.py does not exempt safe methods, so a GET
    // without the header is a 403 rather than a success.
    setCsrfToken("token-abc");
    stubFetch(() => jsonResponse({ ok: true }));

    await apiRequest("openapi", { method: "GET" });

    const headers = calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("token-abc");
  });

  it("sends the session cookie", async () => {
    stubFetch(() => jsonResponse({ ok: true }));
    await apiRequest("whoami");
    expect(calls[0]?.[1]?.credentials).toBe("same-origin");
  });

  it("sends an empty JSON object when a POST has no body", async () => {
    // Handlers call request.get_json() and fall back to {} only when the body
    // parses; an absent body with a JSON content type is not well-formed.
    stubFetch(() => jsonResponse({ ok: true }));
    await apiRequest("whoami");
    expect(calls[0]?.[1]?.body).toBe("{}");
  });

  it("raises ApiError with a plain message for a permission denial", async () => {
    stubFetch(() =>
      jsonResponse(
        { ok: false, error: "Your role does not grant the required permission: plugins.manage." },
        403,
      ),
    );

    await expect(apiRequest("plugins")).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
      message: NO_ACCESS_MESSAGE,
    });
  });

  it("reads plain-text error bodies, which unhandled backend errors return", async () => {
    // handle_request returns text/plain with status 500 for anything unexpected.
    stubFetch(() => textResponse("Traceback: something broke", 500));

    const error = await apiRequest("poll").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    // The headline is a sentence; the server's text is kept for "Technical details".
    expect((error as ApiError).message).toMatch(/^Something went wrong on the server \(HTTP 500\)/);
    expect((error as ApiError).message).not.toContain("Traceback");
    expect((error as ApiError).detail).toBe("Traceback: something broke");
  });

  it("says no access, not the permission code, on a 403", async () => {
    stubFetch(() => jsonResponse({ ok: false, error: "Your role does not grant the required permission: users.manage." }, 403));
    const error = (await apiRequest("users_list").catch((e: unknown) => e)) as ApiError;
    expect(error.message).toBe(NO_ACCESS_MESSAGE);
    expect(error.message).not.toContain("users.manage");
    expect(error.detail).toContain("users.manage");
  });

  it("distinguishes an expired session from a permission denial", async () => {
    stubPage("/app/chat");
    stubFetch(() => jsonResponse({ ok: false, error: "Authentication required." }, 401));
    const error = await apiRequest("whoami").catch((e: unknown) => e as ApiError);
    expect((error as ApiError).isUnauthenticated).toBe(true);
    expect((error as ApiError).isForbidden).toBe(false);
  });

  it("sends the browser to sign in when the session has ended", async () => {
    // A session ends under an open page: the cookie expires, the password is changed elsewhere, the account is
    // disabled. Every call then answers 401, and the page used to show "Authentication required." in each of
    // its panels for as long as it stayed open, with nothing leading back to the sign-in form.
    const assign = stubPage("/app/chat", "?ctx=abc");
    stubFetch(() => jsonResponse({ ok: false, error: "Authentication required." }, 401));

    await apiRequest("poll").catch(() => undefined);

    expect(assign).toHaveBeenCalledWith("/login?next=%2Fapp%2Fchat%3Fctx%3Dabc");
  });

  it("retries once with a fresh token when the CSRF token is stale", async () => {
    setCsrfToken("stale");
    let attempt = 0;
    stubFetch((url) => {
      if (url === "/api/csrf_token") return jsonResponse({ ok: true, token: "fresh" });
      attempt += 1;
      if (attempt === 1) return textResponse("CSRF token missing or invalid", 403);
      return jsonResponse({ ok: true, retried: true });
    });

    const result = await apiRequest<{ retried: boolean }>("settings_set");

    expect(result.retried).toBe(true);
    expect(getCsrfToken()).toBe("fresh");
  });

  it("does not retry a genuine permission denial as if it were a CSRF failure", async () => {
    setCsrfToken("token");
    let attempts = 0;
    stubFetch(() => {
      attempts += 1;
      return jsonResponse({ ok: false, error: "Your role does not grant..." }, 403);
    });

    await expect(apiRequest("users_create")).rejects.toBeInstanceOf(ApiError);
    expect(attempts).toBe(1);
  });
});

describe("loadSession", () => {
  it("does not send a CSRF token, because none exists before bootstrap", async () => {
    // /api/csrf_token itself requires a session, so bootstrap has to be callable
    // without one or a cold start could never begin.
    stubFetch(() =>
      jsonResponse({
        ok: true,
        authenticated: false,
        version: { version: "v1", commit_time: "t" },
      }),
    );

    await loadSession();

    const headers = calls[0]?.[1]?.headers as Record<string, string> | undefined;
    expect(headers?.["X-CSRF-Token"]).toBeUndefined();
  });

  it("returns an anonymous session and clears any stale token", async () => {
    setCsrfToken("left-over");
    stubFetch(() =>
      jsonResponse({
        ok: true,
        authenticated: false,
        version: { version: "v1", commit_time: "t" },
      }),
    );

    const session = await loadSession();

    expect(session.authenticated).toBe(false);
    expect(getCsrfToken()).toBeNull();
  });

  it("adopts the CSRF token from the payload so no second request is needed", async () => {
    stubFetch(() =>
      jsonResponse({
        ok: true,
        authenticated: true,
        version: { version: "v1", commit_time: "t" },
        user: {
          id: "u1",
          username: "admin",
          display_name: "Administrator",
          email: "",
          role: "admin",
          role_title: "Administrator",
          must_change_password: false,
        },
        permissions: ["settings.manage"],
        runtime: { runtime_id: "r1", is_development: true, is_dockerized: false },
        csrf_token: "from-bootstrap",
        preferences: { timezone: "UTC", time_format: "12h", ui_control_visibility: {} },
        webui_extensions: null,
      }),
    );

    const session = await loadSession();

    expect(session.authenticated).toBe(true);
    expect(getCsrfToken()).toBe("from-bootstrap");
    expect(calls).toHaveLength(1);
  });

  it("fails loudly on an authenticated payload that is missing fields", async () => {
    // Half a session is worse than none: the app would render controls whose
    // permissions it cannot actually check.
    stubFetch(() =>
      jsonResponse({
        ok: true,
        authenticated: true,
        version: { version: "v1", commit_time: "t" },
        user: {
          id: "u1",
          username: "admin",
          display_name: "Administrator",
          email: "",
          role: "admin",
          role_title: "Administrator",
          must_change_password: false,
        },
      }),
    );

    await expect(loadSession()).rejects.toThrow(/Incomplete bootstrap payload/);
  });
});

describe("failureMessageIn", () => {
  it("treats an explicit ok:false as failure", () => {
    expect(failureMessageIn({ ok: false, error: "Password must be at least 12 characters." }))
      .toBe("Password must be at least 12 characters.");
  });

  it("treats ok:false with no message as failure anyway", () => {
    expect(failureMessageIn({ ok: false })).toBe("The server rejected that.");
  });

  it("treats a lone error string as failure", () => {
    // The work-directory shape: `{"error": "Parent path is required"}`.
    expect(failureMessageIn({ error: "Parent path is required" }))
      .toBe("Parent path is required");
  });

  it("does not treat a fallback payload carrying an error as failure", () => {
    // model_search answers with a registry fallback plus the reason the live
    // lookup failed. Throwing here would break a working screen to fix a lying
    // one, which is why the rule is narrow.
    expect(failureMessageIn({ models: ["a"], provider: "ollama", error: "Name or service not known" }))
      .toBeNull();
  });

  it("does not treat a null error as failure", () => {
    // /api/health answers {gitinfo, error: null} on success.
    expect(failureMessageIn({ gitinfo: { branch: "main" }, error: null })).toBeNull();
  });

  it("does not treat an empty error as failure", () => {
    expect(failureMessageIn({ error: "" })).toBeNull();
    expect(failureMessageIn({ error: "   " })).toBeNull();
  });

  it("passes a success body through", () => {
    expect(failureMessageIn({ ok: true, user: { username: "a" } })).toBeNull();
    expect(failureMessageIn({ data: { entries: [] } })).toBeNull();
  });

  it("ignores bodies that are not objects", () => {
    expect(failureMessageIn(null)).toBeNull();
    expect(failureMessageIn("plain text")).toBeNull();
    expect(failureMessageIn([{ error: "x" }])).toBeNull();
  });
});

describe("apiAction", () => {
  it("throws when the body says the thing did not happen", async () => {
    // The bug as a user met it: HTTP 200, nothing created, "admin created."
    stubFetch(() =>
      jsonResponse({ ok: false, error: "Password must be at least 12 characters." }),
    );

    await expect(apiAction("users_create", { body: {} })).rejects.toThrow(
      "Password must be at least 12 characters.",
    );
  });

  it("throws on the work-directory failure shape", async () => {
    stubFetch(() => jsonResponse({ error: "Parent path is required" }));

    await expect(apiAction("rename_work_dir_file", { body: {} })).rejects.toThrow(
      "Parent path is required",
    );
  });

  it("returns the body when the thing did happen", async () => {
    stubFetch(() => jsonResponse({ ok: true, user: { username: "new" } }));

    await expect(apiAction("users_create", { body: {} })).resolves.toEqual({
      ok: true,
      user: { username: "new" },
    });
  });

  it("still throws an ApiError carrying the body, so callers can inspect it", async () => {
    stubFetch(() => jsonResponse({ ok: false, error: "nope" }));

    await expect(apiAction("users_create", { body: {} })).rejects.toBeInstanceOf(ApiError);
  });
});

describe("apiDownload", () => {
  it("throws the handler's own sentence when it answers 200 with {ok: false}, instead of saving it as the file", async () => {
    stubFetch(() => jsonResponse({ ok: false, error: "There is no issued pdf document for this change." }));
    await expect(
      apiDownload("plugins/_ea_azure_change/azure_document", { method: "POST", body: {}, filename: "x.pdf" }),
    ).rejects.toThrow("There is no issued pdf document for this change.");
  });
});
