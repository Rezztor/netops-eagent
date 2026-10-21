/**
 * The index's behaviour, tested through the registry rather than the DOM.
 *
 * Every chip, every attention line and every visibility rule is a pure
 * function of the loaded payloads, which is the point of declaring them in the
 * registry instead of scattering them through render code. So they can be
 * checked here without a component harness.
 *
 * Two properties matter more than the individual cases. A chip must never be
 * drawn from a payload that has not loaded -- the index is only worth having
 * if it can be believed without opening the section -- and `attention` must
 * stay quiet unless something is actually wrong, because a warning that is
 * always on is how a real one gets missed.
 */

import { describe, expect, it } from "vitest";

import { collectAttention, matchesQuery, visibleSections } from "../filter";
import { SECTIONS } from "../registry";
import type { SettingsContext } from "../types";

const EMPTY: SettingsContext = {
  // Only the fields the registry reads. The screen passes the real bootstrap.
  session: { licence: null, permissions: [] } as unknown as SettingsContext["session"],
  settings: null,
  solarwinds: null,
  siem: null,
  network: null,
  azure: null,
  reload: () => {},
};

function withSettings(settings: Record<string, unknown>): SettingsContext {
  return { ...EMPTY, settings: { settings, additional: {} } };
}

const section = (id: string) => {
  const found = SECTIONS.find((entry) => entry.id === id);
  if (!found) throw new Error(`no section ${id}`);
  return found;
};

describe("chips", () => {
  it("draws nothing while the payload has not loaded", () => {
    // The whole contract of the index: no data, no claim.
    for (const entry of SECTIONS) {
      expect(entry.chip?.(EMPTY) ?? null, `${entry.id} guessed a chip`).toBeNull();
    }
  });

  it("counts the protocol servers that are on", () => {
    const chip = section("interfaces").chip!;
    expect(chip(withSettings({ mcp_server_enabled: true, a2a_server_enabled: true }))).toEqual({
      tone: "ok",
      text: "2 on",
    });
    expect(chip(withSettings({ mcp_server_enabled: true }))).toEqual({ tone: "ok", text: "1 on" });
    // Access logs are not a protocol server and must not be counted as one.
    expect(chip(withSettings({ uvicorn_access_logs_enabled: true }))).toEqual({
      tone: "muted",
      text: "off",
    });
  });

  it("warns when mail is off", () => {
    const chip = section("email").chip!;
    expect(chip(withSettings({ smtp_enabled: true }))).toEqual({ tone: "ok", text: "on" });
    expect(chip(withSettings({ smtp_enabled: false }))).toEqual({ tone: "warn", text: "off" });
  });

  it("counts only providers that actually have a key", () => {
    const chip = section("model-providers").chip!;
    expect(chip(withSettings({ api_keys: { anthropic: "k", openai: "", other: null } }))).toEqual({
      tone: "ok",
      text: "1 set",
    });
    // Seen in the lab with a local Ollama in use: "Model providers: none", in the warning colour.
    expect(chip(withSettings({ api_keys: {} }))).toEqual({ tone: "muted", text: "no key set" });
  });

  it("says SolarWinds is configured, never that it is reachable", () => {
    const chip = section("solarwinds").chip!;
    const ctx = {
      ...EMPTY,
      solarwinds: { config: { host: "orion.example.com" }, passwordSet: true },
    } as unknown as SettingsContext;
    const result = chip(ctx);
    expect(result).toEqual({ tone: "ok", text: "configured" });
    // Nothing here has asked SolarWinds whether it answers, so the chip must
    // not imply that it has.
    expect(result?.text).not.toContain("connected");
  });

  it("reports SIEM failures ahead of the send count", () => {
    const chip = section("siem-forwarding").chip!;
    const siem = (config: object, stats: object) =>
      ({ ...EMPTY, siem: { config, stats } }) as unknown as SettingsContext;

    expect(chip(siem({ enabled: false }, { sent: 0, send_errors: 0, dropped_queue_full: 0 })))
      .toEqual({ tone: "muted", text: "off" });
    expect(chip(siem({ enabled: true }, { sent: 90, send_errors: 3, dropped_queue_full: 0 })))
      .toEqual({ tone: "crit", text: "3 errors" });
    expect(chip(siem({ enabled: true }, { sent: 90, send_errors: 0, dropped_queue_full: 4 })))
      .toEqual({ tone: "warn", text: "4 dropped" });
    expect(chip(siem({ enabled: true }, { sent: 90, send_errors: 0, dropped_queue_full: 0 })))
      .toEqual({ tone: "ok", text: "90 sent" });
  });

  it("takes the licence chip from the bootstrap payload, not a request", () => {
    const chip = section("licence").chip!;
    const ctx = (licence: object) =>
      ({ ...EMPTY, session: { licence } }) as unknown as SettingsContext;

    expect(chip(ctx({ state: "licensed", grace_days_remaining: null }))).toEqual({
      tone: "ok",
      text: "licensed",
    });
    expect(chip(ctx({ state: "grace1", grace_days_remaining: 6 }))).toEqual({
      tone: "warn",
      text: "grace 6d",
    });
    expect(chip(ctx({ state: "restricted", grace_days_remaining: null }))).toEqual({
      tone: "crit",
      text: "restricted",
    });
  });

  it("takes the updates chip from the bootstrap payload when a restart waits", () => {
    const chip = section("updates").chip!;
    const ctx = (update: object | null) =>
      ({ ...EMPTY, session: { licence: null, update } }) as unknown as SettingsContext;

    expect(chip(ctx(null))).toBeNull();
    expect(chip(ctx({ phase: "idle", version: "2026.10.12" }))).toBeNull();
    expect(chip(ctx({ phase: "staged", version: "2026.10.12" }))).toEqual({
      tone: "warn",
      text: "restart needed",
    });
    const listed = { ...EMPTY, session: { licence: null, update: null, updatesAvailable: 2 } } as unknown as SettingsContext;
    expect(chip(listed)).toEqual({ tone: "accent", text: "2 available" });
  });
});

describe("attention", () => {
  it("stays silent on a healthy instance", () => {
    const healthy = {
      ...withSettings({ smtp_enabled: true, password_reset_enabled: true }),
      session: { licence: { state: "licensed", grace_days_remaining: null } },
      siem: { config: { enabled: true }, stats: { sent: 5, send_errors: 0, dropped_queue_full: 0 } },
    } as unknown as SettingsContext;
    expect(collectAttention(SECTIONS, healthy)).toEqual([]);
  });

  it("says nothing about mail that is simply switched off", () => {
    // A deployment that does not send mail is a choice, not a fault. The
    // warning is only for the contradiction below.
    const ctx = withSettings({ smtp_enabled: false, password_reset_enabled: false });
    expect(section("email").attention!(ctx)).toBeNull();
  });

  it("flags reset links that cannot be sent", () => {
    const ctx = withSettings({ smtp_enabled: false, password_reset_enabled: true });
    expect(section("email").attention!(ctx)).toMatch(/reset .* email is off/i);
  });

  it("flags a licence in grace with the days left", () => {
    const ctx = {
      ...EMPTY,
      session: { licence: { state: "grace2", grace_days_remaining: 1 } },
    } as unknown as SettingsContext;
    // Singular, because "in 1 days" is the kind of thing people notice.
    expect(section("licence").attention!(ctx)).toContain("1 day.");
    const restricted = {
      ...EMPTY,
      session: { licence: { state: "restricted", grace_days_remaining: null, reason: "No licence is installed." } },
    } as unknown as SettingsContext;
    expect(section("licence").attention!(restricted)).toBe("Restricted: No licence is installed.");
  });

  it("flags a staged update that waits for a restart", () => {
    const ctx = {
      ...EMPTY,
      session: { licence: null, update: { phase: "staged", version: "2026.10.12" } },
    } as unknown as SettingsContext;
    expect(section("updates").attention!(ctx)).toBe(
      "Update 2026.10.12 is installed and waits for a restart.",
    );
  });

  it("flags network automation that can run jobs but cannot edit devices", () => {
    const ctx = {
      ...EMPTY,
      network: {
        config: {
          executor_url: "http://ansible:8500",
          executor_token_set: true,
          admin_token_set: false,
        },
      },
    } as unknown as SettingsContext;
    expect(section("network-automation").attention!(ctx)).toMatch(/administration token/i);
  });

  it("keeps the job-token warning when both tokens are missing", () => {
    const ctx = {
      ...EMPTY,
      network: {
        config: {
          executor_url: "http://ansible:8500",
          executor_token_set: false,
          admin_token_set: false,
        },
      },
    } as unknown as SettingsContext;
    expect(section("network-automation").attention!(ctx)).toMatch(/no token/);
    expect(section("network-automation").attention!(ctx)).not.toMatch(/administration/);
  });
});

describe("visibility", () => {
  const all = new Set(["settings.manage", "plugins.manage"]);

  it("hides sections whose permission the account does not hold", () => {
    const ids = visibleSections(SECTIONS, EMPTY, new Set(), true).map((s) => s.id);
    expect(ids).toContain("appearance");
    expect(ids).not.toContain("licence");
    expect(ids).not.toContain("solarwinds");
  });

  it("hides SIEM forwarding when its plugin is switched off", () => {
    const off = {
      ...EMPTY,
      siem: { plugin_enabled: false, config: {}, stats: {} },
    } as unknown as SettingsContext;
    expect(visibleSections(SECTIONS, off, all).map((s) => s.id)).not.toContain("siem-forwarding");

    const on = {
      ...EMPTY,
      siem: { plugin_enabled: true, config: {}, stats: {} },
    } as unknown as SettingsContext;
    expect(visibleSections(SECTIONS, on, all).map((s) => s.id)).toContain("siem-forwarding");
  });

  it("keeps a self-hiding section listed until its payload has loaded", () => {
    // Otherwise the row appears a moment after the index paints, which reads
    // as the interface changing its mind.
    expect(visibleSections(SECTIONS, EMPTY, all, true).map((s) => s.id)).toContain(
      "siem-forwarding",
    );
  });
});

describe("filter", () => {
  it("finds a section by a word that is not in its title", () => {
    expect(matchesQuery(section("email"), "smtp")).toBe(true);
    expect(matchesQuery(section("siem-forwarding"), "syslog")).toBe(true);
    expect(matchesQuery(section("solarwinds"), "orion")).toBe(true);
    expect(matchesQuery(section("model-providers"), "api key")).toBe(true);
  });

  it("ignores case and surrounding space", () => {
    expect(matchesQuery(section("licence"), "  LICENCE  ")).toBe(true);
  });

  it("matches everything when the box is empty", () => {
    expect(SECTIONS.every((entry) => matchesQuery(entry, ""))).toBe(true);
  });

  it("matches nothing that does not mention the term", () => {
    expect(matchesQuery(section("appearance"), "syslog")).toBe(false);
  });
});

describe("azure", () => {
  const credential = (expires_on: string) => ({ kind: "certificate", set: true, fingerprint: "AB:CD", expires_on });
  const azure = (enabled: boolean, tenant_id: string, cred = credential("")) =>
    ({ ...EMPTY, azure: { enabled, config: { tenant_id }, credential: cred } }) as unknown as SettingsContext;
  const inDays = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

  it("is not offered without plugins.manage, nor while the plugin is off", () => {
    const on = azure(true, "t");
    expect(visibleSections(SECTIONS, on, new Set(["settings.manage"])).map((s) => s.id)).not.toContain("azure");
    expect(visibleSections(SECTIONS, on, new Set(["plugins.manage"])).map((s) => s.id)).toContain("azure");
    expect(visibleSections(SECTIONS, azure(false, "t"), new Set(["plugins.manage"])).map((s) => s.id)).not.toContain("azure");
  });

  it("says configured only for a credential and a tenant, and nothing before it loads", () => {
    const chip = section("azure").chip!;
    expect(chip(EMPTY)).toBeNull();
    expect(chip(azure(true, ""))).toEqual({ tone: "muted", text: "not set up" });
    expect(chip(azure(true, "t"))).toEqual({ tone: "ok", text: "configured" });
  });

  it("asks for attention only within 30 days of the credential expiring", () => {
    const attention = section("azure").attention!;
    expect(attention(azure(true, "t", credential(inDays(200))))).toBeNull();
    expect(attention(azure(true, "t", credential("")))).toBeNull();
    expect(attention(azure(true, "t", credential(inDays(10))))).toMatch(/expires in \d+ days/);
    expect(attention(azure(true, "t", credential(inDays(-3))))).toMatch(/expired/);
  });
});
