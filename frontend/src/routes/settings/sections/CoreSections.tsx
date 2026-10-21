/**
 * The sections that read from the shared payloads: personal preferences, and
 * the core platform settings that come from one `settings_get`.
 *
 * These live together because they share a data source, not because they are
 * related. A section with its own endpoint gets its own file -- which is the
 * shape every plugin section takes.
 *
 * Read-only rows are read-only here exactly as they were before this screen was
 * reorganised. Making "Agent profile" or the working directory editable is a
 * change to what the screen can do, not to how it is laid out, and is not
 * smuggled in with a redesign.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { saveSettings } from "@/api/admin";
import { Button, Card, Mono, Row, Select, Tag, Toggle } from "@/components";
import { applyTheme, readThemeChoice, type ThemeChoice } from "@/lib/theme";
import { SaveBar } from "../parts";
import type { SettingsContext } from "../types";

/** Reads a settings value as display text, the way the old screen did. */
function reader(settings: Record<string, unknown>) {
  return (key: string): string => {
    const raw = settings[key];
    if (raw === undefined || raw === null || raw === "") return "";
    if (typeof raw === "boolean") return raw ? "on" : "off";
    return String(raw);
  };
}

/* ---------- Personal ---------- */

export function AppearanceSection() {
  const [theme, setTheme] = useState<ThemeChoice>(() => readThemeChoice());

  return (
    <Card title="Appearance" description="Stored in this browser, not on the server.">
      <Row label="Colour scheme" help="Follows your operating system unless you pick one.">
        <Select
          value={theme}
          onChange={(event) => {
            const next = event.target.value as ThemeChoice;
            applyTheme(next);
            setTheme(next);
          }}
          aria-label="Colour scheme"
        >
          <option value="system">Match system</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </Select>
      </Row>
    </Card>
  );
}

export function LanguageSection({ ctx }: { ctx: SettingsContext }) {
  return (
    <Card
      title="Language and time"
      description="Delivered by /api/bootstrap, so it needs no administrator permission."
    >
      <Row label="Timezone" help="Used for every timestamp shown to you.">
        <Mono>{ctx.session.preferences.timezone}</Mono>
      </Row>
      <Row label="Time format">
        <Tag tone="muted">{ctx.session.preferences.time_format}</Tag>
      </Row>
    </Card>
  );
}

export function AccountSection({ ctx }: { ctx: SettingsContext }) {
  const user = ctx.session.user;
  return (
    <Card title="Account">
      <Row label="Username">
        <Mono>{user.username}</Mono>
      </Row>
      <Row label="Email">
        {user.email || <span style={{ color: "var(--ink-3)" }}>not set</span>}
      </Row>
      <Row label="Role" help="Only an administrator can change this.">
        <Tag tone="muted">{user.role_title}</Tag>
      </Row>
    </Card>
  );
}

/* ---------- Administration ---------- */

export function AgentDefaultsSection({ ctx }: { ctx: SettingsContext }) {
  if (!ctx.settings) return null;
  const value = reader(ctx.settings.settings);
  return (
    <Card title="Agent defaults" description="Applied to every new conversation.">
      <Row label="Agent profile" help="Which prompt and tool set new chats start from.">
        <Mono>{value("agent_profile") || "primary"}</Mono>
      </Row>
      <Row label="Knowledge subdirectory">
        <Mono>{value("agent_knowledge_subdir") || "—"}</Mono>
      </Row>
      <Row label="Chat inherits project">
        <Tag tone="muted">{value("chat_inherit_project")}</Tag>
      </Row>
    </Card>
  );
}

export function WorkingDirectorySection({ ctx }: { ctx: SettingsContext }) {
  if (!ctx.settings) return null;
  const value = reader(ctx.settings.settings);
  return (
    <Card title="Working directory" description="Where the agent reads and writes files.">
      <Row label="Path">
        <Mono>{value("workdir_path")}</Mono>
      </Row>
      <Row label="Listing limits" help="Bounds how much structure is put in the prompt.">
        <span style={{ color: "var(--ink-2)" }}>
          depth {value("workdir_max_depth")} · {value("workdir_max_files")} files ·{" "}
          {value("workdir_max_folders")} folders
        </span>
      </Row>
    </Card>
  );
}

/**
 * The one core section that writes.
 *
 * `settings_set` merges rather than replaces -- `convert_in` starts from the
 * current document and applies only the keys sent -- so a screen showing a
 * subset can save that subset without dropping the 30 fields it does not
 * render, provider credentials included. Verified against a running instance
 * before this was wired up, because the opposite behaviour would have destroyed
 * configuration silently.
 */
export function InterfacesSection({ ctx }: { ctx: SettingsContext }) {
  const settings = ctx.settings?.settings ?? {};
  const initial = {
    mcp_server_enabled: Boolean(settings["mcp_server_enabled"]),
    a2a_server_enabled: Boolean(settings["a2a_server_enabled"]),
    uvicorn_access_logs_enabled: Boolean(settings["uvicorn_access_logs_enabled"]),
  };

  const [values, setValues] = useState<Record<string, boolean>>(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  if (!ctx.settings) return null;

  const changed = Object.entries(values).filter(
    ([key, value]) => Boolean(settings[key]) !== value,
  );

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await saveSettings({ settings: Object.fromEntries(changed) });
      setSaved(true);
      ctx.reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      // Put the switches back where the server still has them.
      setValues(initial);
    } finally {
      setSaving(false);
    }
  }

  const set = (key: string) => (next: boolean) => {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: next }));
  };

  return (
    <>
      <Card title="Interfaces" description="Protocol servers this instance exposes.">
        <Row label="MCP server" help="Lets external MCP clients call this agent.">
          <Toggle
            checked={values["mcp_server_enabled"] ?? false}
            onChange={set("mcp_server_enabled")}
            label="MCP server"
            disabled={saving}
          />
        </Row>
        <Row label="A2A server" help="Agent-to-agent protocol endpoint.">
          <Toggle
            checked={values["a2a_server_enabled"] ?? false}
            onChange={set("a2a_server_enabled")}
            label="A2A server"
            disabled={saving}
          />
        </Row>
        <Row
          label="Access logs"
          help="Uvicorn request logging. Off by default; useful when diagnosing, noisy otherwise."
        >
          <Toggle
            checked={values["uvicorn_access_logs_enabled"] ?? false}
            onChange={set("uvicorn_access_logs_enabled")}
            label="Access logs"
            disabled={saving}
          />
        </Row>
      </Card>
      <SaveBar
        dirty={changed.length}
        saving={saving}
        saved={saved}
        error={error}
        onSave={() => void save()}
        onDiscard={() => {
          setValues(initial);
          setError(null);
        }}
      />
    </>
  );
}

/**
 * A pointer, not a second place to edit keys.
 *
 * The Models screen owns provider credentials and is the only screen that
 * reports where each key came from -- a key set in the interface is written to
 * `usr/.env`, which `load_dotenv(override=True)` makes win over an injected
 * environment variable, so a rotated deployment variable can be shadowed with
 * nothing to say so. Two editable copies of that is precisely the duplication
 * the settings reorganisation set out to remove when `Plugins` appeared under
 * two tabs; this entry keeps the setting findable from Settings without
 * creating one.
 */
export function ModelProvidersSection({ ctx }: { ctx: SettingsContext }) {
  const navigate = useNavigate();
  if (!ctx.settings) return null;
  const apiKeys = (ctx.settings.settings["api_keys"] ?? {}) as Record<string, unknown>;
  const configured = Object.entries(apiKeys)
    .filter(([, key]) => typeof key === "string" && key.length > 0)
    .map(([provider]) => provider);

  return (
    <Card
      title="Model providers"
      description="Managed on the Models screen, which reports where each key came from. Values are never displayed here or there — only whether a key is set."
      actions={
        <Button variant="primary" onClick={() => navigate("/models")}>
          Open Models
        </Button>
      }
    >
      <Row
        label="Providers with a key"
        help="A provider with a key set through the interface or injected from the environment. A provider that needs no key, such as a local Ollama, is not listed here: the Models screen shows which model is in use."
      >
        {configured.length ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {configured.map((provider) => (
              <Tag key={provider} tone="ok">
                {provider}
              </Tag>
            ))}
          </div>
        ) : (
          <Tag>no key set</Tag>
        )}
      </Row>
    </Card>
  );
}
