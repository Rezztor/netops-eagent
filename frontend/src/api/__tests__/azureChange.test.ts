import { afterEach, describe, expect, it, vi } from "vitest";

import { getAzureChanges, makeKey } from "../azureChange";
import { ApiError } from "../client";

const answer = (body: unknown) =>
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }))));

afterEach(() => vi.unstubAllGlobals());

describe("the Azure change answers the screens read", () => {
  it("reports a switched-off plugin with nothing to show as absent (404), as the Changes tab expects", async () => {
    answer({ ok: true, enabled: false, changes: [], truncated: false });
    await expect(getAzureChanges()).rejects.toMatchObject({ status: 404 });
    await expect(getAzureChanges()).rejects.toBeInstanceOf(ApiError);
  });

  it("still lists the history of a plugin switched off after use", async () => {
    answer({ ok: true, enabled: false, changes: [{ id: "c1" }] });
    await expect(getAzureChanges()).resolves.toHaveLength(1);
  });

  it("sends the key's name as `role`, which is what the endpoint reads", async () => {
    answer({ ok: true, public_certificate: "PEM", thumbprint: "AB", expires_on: "2027-10-03" });
    await makeKey("planner", "t", "c");
    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({ action: "make_key", role: "planner" });
  });
});
