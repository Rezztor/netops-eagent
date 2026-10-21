/**
 * `searchModels` reports why it could not ask, not only what it got.
 *
 * The endpoint can answer with both: a registry fallback list *and* the reason
 * the provider itself was unreachable --
 * `{models: [...], error: "[Errno -2] Name or service not known"}`. This
 * returned a bare array and dropped the message, so pointing the form at a
 * provider whose server was not running looked exactly like pointing it at one
 * that was: a list of models appeared, none of which the deployment could use.
 * Reported as G4 from a GUI QA pass on 2026-09-19.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { searchModels } from "../models";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(handler: () => Response | Promise<Response>) {
  vi.stubGlobal("fetch", () => Promise.resolve(handler()));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("searchModels", () => {
  it("keeps the reason when the provider could not be reached", async () => {
    // The exact shape from the report: a fallback list plus the reason.
    stubFetch(() =>
      jsonResponse({
        models: [{ name: "llama3", ctx_length: 8192 }],
        provider: "ollama",
        source: "registry",
        error: "[Errno -2] Name or service not known",
      }),
    );

    const outcome = await searchModels("ollama");

    expect(outcome.error).toBe("[Errno -2] Name or service not known");
    // And the fallback survives -- it is still the best guess available, which
    // is why throwing on this payload would have been the wrong fix.
    expect(outcome.models).toHaveLength(1);
  });

  it("reports no error when the provider answered", async () => {
    stubFetch(() => jsonResponse({ models: [{ name: "gpt-5.3" }], error: "" }));

    const outcome = await searchModels("openai");

    expect(outcome.error).toBeNull();
    expect(outcome.models).toHaveLength(1);
  });

  it("treats a missing error field as no error", async () => {
    stubFetch(() => jsonResponse({ models: [] }));

    await expect(searchModels("openai")).resolves.toEqual({ models: [], error: null });
  });

  it("reports a transport failure rather than looking like an empty catalogue", async () => {
    // "no suggestions" and "could not ask" are different answers. The old code
    // swallowed the throw and returned [], which made them identical.
    stubFetch(() => jsonResponse({ error: "boom" }, 500));

    const outcome = await searchModels("openai");

    expect(outcome.models).toEqual([]);
    expect(outcome.error).toBeTruthy();
  });
});
