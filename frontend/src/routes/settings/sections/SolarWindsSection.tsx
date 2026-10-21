/**
 * The SolarWinds plugin's connection.
 *
 * Its own endpoint, not core settings -- this decides which SolarWinds instance
 * the agent reads live infrastructure data from, and it is the plugin's own
 * config, not a core Settings field. Gated on `plugins.manage` specifically:
 * holding `settings.manage` (what the rest of this screen runs on) is not
 * enough to reach it server-side, so the registry declares the stricter
 * permission and the section is not offered without it -- the same rule the
 * main navigation follows, for the same reason.
 */

import { useState } from "react";

import { saveSolarWindsConfig, type SolarWindsConfig } from "@/api/solarwinds";
import { Card, Row, TextInput, Toggle } from "@/components";
import { SaveBar } from "../parts";
import type { SettingsContext } from "../types";

function toFormValues(config: SolarWindsConfig | undefined) {
  return {
    host: config?.host ?? "",
    port: String(config?.port ?? 17778),
    username: config?.username ?? "",
    timeout_seconds: String(config?.timeout_seconds ?? 15),
    max_rows: String(config?.max_rows ?? 50),
    max_calls_per_minute: String(config?.max_calls_per_minute ?? 20),
  };
}

export function SolarWindsSection({ ctx }: { ctx: SettingsContext }) {
  const initial = ctx.solarwinds;

  // The saved baseline, not just the initial load -- state, not a plain const,
  // so a second save in the same visit can also go from dirty back to clean.
  // Without this the save bar would never go away again after the first edit:
  // `dirty` would keep comparing against what the page loaded with, forever,
  // even though the server already has the new value.
  const [base, setBase] = useState(() => toFormValues(initial?.config));
  const [baseVerifyTls, setBaseVerifyTls] = useState(initial?.config.verify_tls !== false);
  const [values, setValues] = useState(base);
  // Always blank on load, deliberately -- the password is never sent to this
  // screen. Left blank on save, it stays whatever it already is.
  const [password, setPassword] = useState("");
  const [verifyTls, setVerifyTls] = useState(initial?.config.verify_tls !== false);
  const [passwordSet, setPasswordSet] = useState(Boolean(initial?.passwordSet));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  if (!initial) return null;

  const dirty =
    Object.entries(values).filter(([key, value]) => base[key as keyof typeof base] !== value)
      .length +
    (verifyTls !== baseVerifyTls ? 1 : 0) +
    (password.length > 0 ? 1 : 0);

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const result = await saveSolarWindsConfig({
        host: values.host,
        port: Number(values.port) || 17778,
        username: values.username,
        timeout_seconds: Number(values.timeout_seconds) || 15,
        max_rows: Number(values.max_rows) || 50,
        max_calls_per_minute: Number(values.max_calls_per_minute) || 20,
        verify_tls: verifyTls,
        ...(password ? { password } : {}),
      });
      setPasswordSet(result.passwordSet);
      setPassword("");
      // Reset the baseline to what the server actually now has, so `dirty`
      // reads clean and the next comparison is against reality, not against
      // whatever the page happened to load with.
      const nextValues = toFormValues(result.config);
      setBase(nextValues);
      setValues(nextValues);
      setBaseVerifyTls(result.config.verify_tls !== false);
      setVerifyTls(result.config.verify_tls !== false);
      setSaved(true);
      ctx.reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  const set = (key: keyof typeof base) => (event: React.ChangeEvent<HTMLInputElement>) => {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: event.target.value }));
  };

  return (
    <>
      <Card
        title="SolarWinds"
        description="Lets the agent read status, alerts, configuration, hardware health, NetFlow and connectivity from one SolarWinds instance. Read-only — it cannot change anything in SolarWinds."
      >
        <Row label="Host" help="Hostname or IP of the SolarWinds instance.">
          <TextInput
            value={values.host}
            onChange={set("host")}
            placeholder="orion.example.com"
            disabled={saving}
          />
        </Row>
        <Row label="Port" help="17778 on current releases; some older instances use 17774.">
          <TextInput
            value={values.port}
            onChange={set("port")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
        <Row
          label="Verify TLS certificate"
          help="Off accepts a self-signed certificate. Turn this off only deliberately, for an instance you know does not have a real one."
        >
          <Toggle
            checked={verifyTls}
            onChange={(next) => {
              setSaved(false);
              setVerifyTls(next);
            }}
            label="Verify TLS certificate"
            disabled={saving}
          />
        </Row>
        <Row
          label="Username"
          help="A dedicated, least-privilege service account — not a personal or admin login."
        >
          <TextInput
            value={values.username}
            onChange={set("username")}
            autoComplete="off"
            disabled={saving}
          />
        </Row>
        <Row
          label="Password"
          help={
            passwordSet
              ? "Already set. Leave blank to keep it; type a new one to replace it."
              : "Not set yet."
          }
        >
          <TextInput
            type="password"
            value={password}
            onChange={(event) => {
              setSaved(false);
              setPassword(event.target.value);
            }}
            autoComplete="new-password"
            placeholder={passwordSet ? "••••••••" : ""}
            disabled={saving}
          />
        </Row>
        <Row label="Timeout" help="Seconds to wait for SolarWinds to answer one query.">
          <TextInput
            value={values.timeout_seconds}
            onChange={set("timeout_seconds")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
        <Row label="Row cap" help="Largest result any single lookup may return.">
          <TextInput
            value={values.max_rows}
            onChange={set("max_rows")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
        <Row
          label="Calls per minute"
          help="Per conversation. Guards against a loop hammering the instance."
        >
          <TextInput
            value={values.max_calls_per_minute}
            onChange={set("max_calls_per_minute")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
      </Card>
      <SaveBar
        dirty={dirty}
        saving={saving}
        saved={saved}
        error={error}
        onSave={() => void save()}
        onDiscard={() => {
          setValues(base);
          setVerifyTls(baseVerifyTls);
          setPassword("");
          setError(null);
        }}
      />
    </>
  );
}
