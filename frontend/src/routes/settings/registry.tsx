/**
 * Every settings section, declared once.
 *
 * This array is the screen. The index, the overview, the routes and the chips
 * are all derived from it, so a new section -- almost always a plugin's
 * configuration -- is a new file under `sections/` plus one entry here, rather
 * than an edit to a screen that every other section also lives in. That was the
 * actual cost of the layout this replaced: eleven cards hard-coded in render
 * order in one 1,199-line file.
 *
 * Two rules for entries:
 *
 * `chip` must be cheap and honest. It is drawn from payloads the screen already
 * loads, never from a probe of the remote system -- "configured" says a host is
 * set, and does not claim SolarWinds answered. A chip that cannot be computed
 * returns null and is not drawn, because a stale green on the index is worse
 * than no chip at all.
 *
 * `attention` is for a state an administrator would act on today. A warning
 * that is always present is noise, and noise is how a real one gets missed --
 * so "SolarWinds is not configured" is not attention-worthy (the plugin may
 * simply be unused) while "reset links are on but mail is off" is, because the
 * two settings contradict each other.
 */

import { PERMISSIONS } from "@/api/types";
import {
  AccountSection,
  AgentDefaultsSection,
  AppearanceSection,
  InterfacesSection,
  LanguageSection,
  ModelProvidersSection,
  WorkingDirectorySection,
} from "./sections/CoreSections";
import { AzureSection } from "./sections/AzureSection";
import { AzureChangesSection } from "./sections/AzureChangesSection";
import { EmailSection } from "./sections/EmailSection";
import { LICENCE_STATE_TONE, LicenceSection } from "./sections/LicenceSection";
import { NetworkAutomationSection } from "./sections/NetworkAutomationSection";
import { SiemForwardSection } from "./sections/SiemForwardSection";
import { SolarWindsSection } from "./sections/SolarWindsSection";
import { SupportBundleSection } from "./sections/SupportBundleSection";
import { BackupSettingsSection, RestartSection } from "./sections/SystemSections";
import { UpdatesSection } from "./sections/UpdatesSection";
import type { SettingsSection } from "./types";

export const SECTIONS: SettingsSection[] = [
  /* ---------- Personal ---------- */
  {
    id: "appearance",
    group: "Personal",
    title: "Appearance",
    summary: "Light, dark, or follow the operating system.",
    keywords: ["theme", "dark mode", "colour", "color"],
    Component: AppearanceSection,
  },
  {
    id: "language",
    group: "Personal",
    title: "Language and time",
    summary: "The timezone and clock format used for every timestamp you see.",
    keywords: ["timezone", "clock", "24h", "12h", "date"],
    Component: LanguageSection,
  },
  {
    id: "account",
    group: "Personal",
    title: "Account",
    summary: "Who you are signed in as, and the role you hold.",
    keywords: ["username", "email", "role", "profile"],
    Component: AccountSection,
  },

  /* ---------- Administration ---------- */
  {
    id: "agent-defaults",
    group: "Administration",
    title: "Agent defaults",
    summary: "The profile and knowledge every new conversation starts from.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["profile", "prompt", "knowledge", "subdirectory"],
    Component: AgentDefaultsSection,
  },
  {
    id: "working-directory",
    group: "Administration",
    title: "Working directory",
    summary: "Where the agent reads and writes files, and how much it may list.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["workdir", "path", "files", "folders", "depth"],
    Component: WorkingDirectorySection,
  },
  {
    id: "interfaces",
    group: "Administration",
    title: "Interfaces",
    summary: "Protocol servers this instance exposes to other systems.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["mcp", "a2a", "protocol", "access logs", "uvicorn"],
    Component: InterfacesSection,
    chip: (ctx) => {
      const settings = ctx.settings?.settings;
      if (!settings) return null;
      const on = ["mcp_server_enabled", "a2a_server_enabled"].filter((key) =>
        Boolean(settings[key]),
      ).length;
      return on ? { tone: "ok", text: `${on} on` } : { tone: "muted", text: "off" };
    },
  },
  {
    id: "azure-changes",
    group: "Administration",
    title: "Azure changes",
    summary: "What the assistant may add or change in Azure: guard rails, the CR or SR number format, keys.",
    permission: PERMISSIONS.pluginsManage,
    keywords: ["cloud", "guard rails", "ceiling", "cost", "cr", "sr", "change request", "reference", "certificate", "role", "opentofu"],
    Component: AzureChangesSection,
  },
  {
    id: "email",
    group: "Administration",
    title: "Email",
    summary: "Reminders and password reset links sent to people with accounts here.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["smtp", "mail", "relay", "password reset", "starttls", "sender"],
    Component: EmailSection,
    chip: (ctx) => {
      const settings = ctx.settings?.settings;
      if (!settings) return null;
      return settings["smtp_enabled"]
        ? { tone: "ok", text: "on" }
        : { tone: "warn", text: "off" };
    },
    attention: (ctx) => {
      const settings = ctx.settings?.settings;
      if (!settings) return null;
      // Only when the two settings contradict each other. Mail deliberately
      // left off is a choice, not a fault.
      if (settings["password_reset_enabled"] !== false && !settings["smtp_enabled"]) {
        return "Self-service password reset is on, but email is off — no reset links can be sent.";
      }
      return null;
    },
  },
  {
    id: "model-providers",
    group: "Administration",
    title: "Model providers",
    summary: "Which providers have a key. Managed on the Models screen.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["api key", "credentials", "anthropic", "openai", "openrouter", "models"],
    Component: ModelProvidersSection,
    chip: (ctx) => {
      const settings = ctx.settings?.settings;
      if (!settings) return null;
      const keys = (settings["api_keys"] ?? {}) as Record<string, unknown>;
      const set = Object.values(keys).filter(
        (key) => typeof key === "string" && key.length > 0,
      ).length;
      // No key is not "no provider": a local Ollama needs none. So this counts keys and warns of nothing.
      return set ? { tone: "ok", text: `${set} set` } : { tone: "muted", text: "no key set" };
    },
  },

  /* ---------- Integrations: the group that absorbs plugins ---------- */
  {
    id: "solarwinds",
    group: "Integrations",
    title: "SolarWinds",
    summary: "Read-only access to one SolarWinds instance for live infrastructure data.",
    permission: PERMISSIONS.pluginsManage,
    keywords: ["orion", "network", "monitoring", "netflow", "alerts", "npm"],
    Component: SolarWindsSection,
    chip: (ctx) => {
      if (!ctx.solarwinds) return null;
      // "configured", not "connected": nothing here has asked SolarWinds
      // whether it answers, and a chip must not imply that it has.
      return ctx.solarwinds.config.host
        ? { tone: "ok", text: "configured" }
        : { tone: "muted", text: "not set up" };
    },
  },
  {
    id: "azure",
    group: "Integrations",
    title: "Azure",
    summary: "Read-only access to one Azure tenant for the cloud inventory.",
    permission: PERMISSIONS.pluginsManage,
    keywords: ["cloud", "microsoft", "entra", "subscription", "tenant", "certificate", "resource graph", "reader"],
    Component: AzureSection,
    // Off in the Plugins screen means there is nothing to configure here.
    visible: (ctx) => ctx.azure?.enabled === true,
    chip: (ctx) => {
      if (!ctx.azure) return null;
      // "configured", not "connected": Test connection is what asks Azure.
      return ctx.azure.credential.set && ctx.azure.config.tenant_id
        ? { tone: "ok", text: "configured" }
        : { tone: "muted", text: "not set up" };
    },
    attention: (ctx) => {
      const credential = ctx.azure?.credential;
      if (!credential?.set || !credential.expires_on) return null;
      const days = Math.ceil((Date.parse(credential.expires_on) - Date.now()) / 86_400_000);
      if (Number.isNaN(days) || days > 30) return null;
      return days < 0
        ? "The Azure credential has expired, so no pull can sign in."
        : `The Azure credential expires in ${days} ${days === 1 ? "day" : "days"}.`;
    },
  },
  {
    id: "network-automation",
    group: "Integrations",
    title: "Network automation",
    summary: "The Ansible executor the agent reaches devices through, and the changes waiting for approval.",
    permission: PERMISSIONS.pluginsManage,
    keywords: [
      "ansible", "network", "switch", "router", "firewall", "cisco", "ios",
      "sonic", "fortigate", "fortinet", "executor", "playbook", "change",
      "approval", "vlan",
    ],
    Component: NetworkAutomationSection,
    chip: (ctx) => {
      if (!ctx.network) return null;
      // "configured" says an address and a token are set. It does not claim the
      // executor answered -- that is what the reachability check is for.
      return ctx.network.config.configured
        ? { tone: "ok", text: "configured" }
        : { tone: "muted", text: "not set up" };
    },
    attention: (ctx) => {
      if (!ctx.network) return null;
      // Half-configured is worth saying: an address with no token cannot
      // authenticate, and every job will be refused with a 401 that looks like
      // an outage rather than a missing setting.
      const { executor_url, executor_token_set, admin_token_set } = ctx.network.config;
      if (executor_url && !executor_token_set) {
        return "Network automation has an executor address but no token, so every job will be refused.";
      }
      if (executor_url && executor_token_set && admin_token_set === false) {
        return "Network automation has no administration token, so the inventory and credentials cannot be edited.";
      }
      return null;
    },
    visible: (ctx) => ctx.network?.plugin_enabled !== false,
  },
  {
    id: "siem-forwarding",
    group: "Integrations",
    title: "SIEM forwarding",
    summary: "A copy of the audit trail, sent to a collector as CEF over UDP syslog.",
    permission: PERMISSIONS.pluginsManage,
    keywords: ["syslog", "cef", "audit", "collector", "splunk", "rfc3164", "rfc5424"],
    Component: SiemForwardSection,
    // Off in the Plugins screen means its configuration is not part of this
    // screen at all, rather than a form that cannot do anything.
    visible: (ctx) => ctx.siem?.plugin_enabled === true,
    chip: (ctx) => {
      if (!ctx.siem) return null;
      const { config, stats } = ctx.siem;
      if (!config.enabled) return { tone: "muted", text: "off" };
      if (stats.send_errors) return { tone: "crit", text: `${stats.send_errors} errors` };
      if (stats.dropped_queue_full) {
        return { tone: "warn", text: `${stats.dropped_queue_full} dropped` };
      }
      return { tone: "ok", text: `${stats.sent} sent` };
    },
    attention: (ctx) => {
      if (!ctx.siem?.config.enabled) return null;
      const { send_errors, dropped_queue_full } = ctx.siem.stats;
      if (send_errors) {
        return `SIEM forwarding: ${send_errors} ${send_errors === 1 ? "send" : "sends"} failed since the last reset.`;
      }
      if (dropped_queue_full) {
        return `SIEM forwarding: ${dropped_queue_full} events dropped — the collector could not keep up.`;
      }
      return null;
    },
  },

  /* ---------- Product ---------- */
  {
    id: "licence",
    group: "Product",
    title: "Licence",
    summary: "What this instance is entitled to run, and its activation.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["licence", "license", "activation", "seats", "devices", "expiry", "offline", "entitlement"],
    Component: LicenceSection,
    // From the bootstrap payload the app-wide banner already uses, so the chip
    // costs no request of its own.
    chip: (ctx) => {
      const licence = ctx.session.licence;
      if (!licence) return null;
      if (licence.state === "community") return { tone: "ok", text: "Community" };
      const tone = LICENCE_STATE_TONE[licence.state];
      if (licence.grace_days_remaining !== null) {
        return { tone, text: `grace ${licence.grace_days_remaining}d` };
      }
      return { tone, text: licence.state };
    },
    attention: (ctx) => {
      const licence = ctx.session.licence;
      if (!licence || licence.state === "community") return null;
      if (licence.grace_days_remaining !== null) {
        const days = licence.grace_days_remaining;
        return `The licence could not be renewed: nothing new starts in ${days} ${days === 1 ? "day" : "days"}.`;
      }
      if (licence.state === "restricted") {
        return `Restricted: ${licence.reason ?? "the licence is not valid."}`;
      }
      // A licensed installation with something to say: a revocation notice.
      return licence.reason ?? null;
    },
  },
  {
    id: "updates",
    group: "Product",
    title: "Updates",
    summary: "Install a fix from Rezztor without installing again.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["update", "upgrade", "rezupd", "package", "restart", "patch", "download", "available"],
    Component: UpdatesSection,
    chip: (ctx) => {
      const update = ctx.session.update;
      if (update?.phase === "staged") return { tone: "warn", text: "restart needed" };
      const count = ctx.session.updatesAvailable ?? 0;
      return count > 0 ? { tone: "accent", text: `${count} available` } : null;
    },
    attention: (ctx) => {
      const update = ctx.session.update;
      if (!update || update.phase !== "staged") return null;
      return `Update ${update.version} is installed and waits for a restart.`;
    },
  },
  {
    id: "backup",
    group: "Product",
    title: "Backup",
    summary: "Make a backup of this instance, check one, or restore from it.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["backup", "restore", "archive", "export"],
    Component: BackupSettingsSection,
  },
  {
    id: "restart",
    group: "Product",
    title: "Restart",
    summary: "Reload the server process after a change read at startup.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["restart", "reload", "lifecycle", "reboot"],
    Component: RestartSection,
  },
  {
    id: "support-bundle",
    group: "Product",
    title: "Support bundle",
    summary: "The logs and settings of this machine in one file, for Rezztor's support.",
    permission: PERMISSIONS.settingsManage,
    keywords: ["support", "bundle", "logs", "diagnostics", "tech support", "rezztor"],
    Component: SupportBundleSection,
  },
];
