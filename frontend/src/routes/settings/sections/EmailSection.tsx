/**
 * Outbound mail.
 *
 * The platform sends to addresses on its own accounts and nowhere else, so this
 * is an internal notification channel rather than a mail client -- there is no
 * "allowed recipients" list to configure because there is no way to name a
 * recipient who is not a member.
 *
 * The password field follows the pattern the RFC password already uses: the
 * stored value is a placeholder, the real one lives in the environment, and
 * sending the placeholder back unchanged leaves it alone.
 */

import { useState } from "react";

import { saveSettings } from "@/api/admin";
import { Card, Row, Select, TextInput, Toggle } from "@/components";
import { SaveBar } from "../parts";
import type { SettingsContext } from "../types";

export function EmailSection({ ctx }: { ctx: SettingsContext }) {
  const settings = ctx.settings?.settings ?? {};
  const initial = {
    smtp_host: String(settings["smtp_host"] ?? ""),
    smtp_port: String(settings["smtp_port"] ?? 587),
    smtp_security: String(settings["smtp_security"] ?? "starttls"),
    smtp_username: String(settings["smtp_username"] ?? ""),
    smtp_password: String(settings["smtp_password"] ?? ""),
    smtp_from_address: String(settings["smtp_from_address"] ?? ""),
    smtp_from_name: String(settings["smtp_from_name"] ?? ""),
    smtp_base_url: String(settings["smtp_base_url"] ?? ""),
  };
  const initialEnabled = Boolean(settings["smtp_enabled"]);
  const initialReset = settings["password_reset_enabled"] !== false;

  const [values, setValues] = useState(initial);
  const [enabled, setEnabled] = useState(initialEnabled);
  const [resetEnabled, setResetEnabled] = useState(initialReset);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const changed = Object.entries(values).filter(
    ([key, value]) => initial[key as keyof typeof initial] !== value,
  );
  const dirty =
    changed.length +
    (initialEnabled !== enabled ? 1 : 0) +
    (initialReset !== resetEnabled ? 1 : 0);

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await saveSettings({
        settings: {
          ...Object.fromEntries(changed),
          smtp_enabled: enabled,
          smtp_port: Number(values.smtp_port) || 587,
          password_reset_enabled: resetEnabled,
        },
      });
      setSaved(true);
      ctx.reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  const set = (key: keyof typeof initial) => (event: React.ChangeEvent<HTMLInputElement>) => {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: event.target.value }));
  };

  return (
    <>
      <Card
        title="Email"
        description="How the platform sends reminders, updates and password reset links to the people who hold accounts here. It cannot send anywhere else."
      >
        <Row label="Send email" help="Off means no reminders and no password reset links.">
          <Toggle
            checked={enabled}
            onChange={(next) => {
              setSaved(false);
              setEnabled(next);
            }}
            label="Send email"
            disabled={saving}
          />
        </Row>
        <Row
          label="Let members reset their own password"
          help="Administrators can always use this, so turning it off cannot lock one out of the platform. Off means everyone else is directed to an administrator instead."
        >
          <Toggle
            checked={resetEnabled}
            onChange={(next) => {
              setSaved(false);
              setResetEnabled(next);
            }}
            label="Self-service password reset"
            disabled={saving}
          />
        </Row>
        <Row label="Server" help="Hostname of the SMTP relay.">
          <TextInput
            value={values.smtp_host}
            onChange={set("smtp_host")}
            placeholder="smtp.example.com"
            disabled={saving}
          />
        </Row>
        <Row label="Port">
          <TextInput
            value={values.smtp_port}
            onChange={set("smtp_port")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
        <Row
          label="Encryption"
          help="STARTTLS on 587, or implicit TLS on 465. Choosing none sends the credentials in the clear, so choose it only for a relay on a trusted network."
        >
          <Select
            value={values.smtp_security}
            onChange={(event) => {
              setSaved(false);
              setValues((current) => ({ ...current, smtp_security: event.target.value }));
            }}
            aria-label="Encryption"
            disabled={saving}
          >
            <option value="starttls">STARTTLS</option>
            <option value="ssl">Implicit TLS</option>
            <option value="none">None</option>
          </Select>
        </Row>
        <Row label="Username" help="Leave empty for a relay that does not authenticate.">
          <TextInput
            value={values.smtp_username}
            onChange={set("smtp_username")}
            autoComplete="off"
            disabled={saving}
          />
        </Row>
        <Row label="Password" help="Stored in the environment, never in the settings file.">
          <TextInput
            type="password"
            value={values.smtp_password}
            onChange={set("smtp_password")}
            autoComplete="new-password"
            disabled={saving}
          />
        </Row>
        <Row label="From address" help="What recipients see, and where replies would go.">
          <TextInput
            value={values.smtp_from_address}
            onChange={set("smtp_from_address")}
            placeholder="eagent@example.com"
            disabled={saving}
          />
        </Row>
        <Row label="From name">
          <TextInput
            value={values.smtp_from_name}
            onChange={set("smtp_from_name")}
            placeholder="Rezztor EAgent"
            disabled={saving}
          />
        </Row>
        <Row
          label="Public address of this instance"
          help="Used to build password reset links. It must be set explicitly: a link built from the request's own Host header is a link an attacker can point at their own server."
        >
          <TextInput
            value={values.smtp_base_url}
            onChange={set("smtp_base_url")}
            placeholder="https://eagent.example.com"
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
          setValues(initial);
          setEnabled(initialEnabled);
          setResetEnabled(initialReset);
          setError(null);
        }}
      />
    </>
  );
}
