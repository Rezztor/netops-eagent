/**
 * The Azure plugin's connection: which tenant, the credential, which subscriptions and when to pull.
 *
 * Its own endpoint (`plugins.manage`), like SolarWinds. The credential is write-only: the secret field is always
 * blank on load and left blank on save it changes nothing; a certificate's private half never reaches the browser, so
 * the only thing this screen can download is the public file. Test connection stores nothing and runs against what
 * is saved, not what is typed.
 */

import { useCallback, useEffect, useState } from "react";

import {
  generateAzureCertificate,
  generateAzureLogsCertificate,
  getAzureLogsPublicCertificate,
  getAzurePublicCertificate,
  getAzureStatus,
  READ_APPROVALS,
  saveAzureConfig,
  testAzureConnection,
  type AzureConfig,
  type AzureLogsConfig,
  type ReadApproval,
} from "@/api/azure";
import { Button, Card, DateTime, Row, Select, Tag, TextInput, useConfirm, useToast } from "@/components";
import { saveExport } from "@/routes/inventory/download";
import { SaveBar } from "../parts";
import type { SettingsContext } from "../types";

/** What a failed sign-in means, in the administrator's words. Never Azure's own text. */
export const SIGN_IN_FAILURES: Record<string, string> = {
  tenant: "Azure does not know that Directory (tenant) ID. Check the value copied from the app's Overview page.",
  application:
    "That Application (client) ID is not registered in this directory. Check the value copied from the app's Overview page.",
  certificate:
    "Azure did not accept the certificate. Upload the public file (.cer) under the app's Certificates & secrets, Certificates.",
  secret: "Azure did not accept the client secret. Paste it again, or create a new one.",
  expired: "The credential has expired. Create a new certificate or secret.",
  network: "Microsoft could not be reached. Allow outgoing HTTPS to login.microsoftonline.com and management.azure.com.",
  other: "Sign-in failed.",
};

const READ_APPROVAL_TEXT: Record<ReadApproval, string> = {
  every_time: "Ask on a card every time (recommended)",
  once_per_chat: "Ask once per resource in a chat",
  log_only: "Do not ask; record it in the audit log",
};

/** One workspace per line: a short name, then its workspace ID. The server checks both and says what is wrong. */
const workspaceLines = (logs?: AzureLogsConfig) => (logs?.workspaces ?? []).map((w) => `${w.name} ${w.id}`).join("\n");
const parseWorkspaces = (text: string) =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name = "", id = ""] = line.split(/[\s=,]+/);
      return { name, id };
    });
const parseTables = (text: string) => text.split(/[\s,]+/).filter(Boolean);

const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const WEEKDAY_NAMES: Record<string, string> = {
  mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday",
};

function toForm(config: AzureConfig) {
  return {
    cloud: config.cloud || "public",
    tenant_id: config.tenant_id ?? "",
    client_id: config.client_id ?? "",
    auth: config.auth ?? "certificate",
    secret_expires_on: config.secret_expires_on ?? "",
    subscriptions_mode: config.subscriptions_mode ?? "all",
    frequency: config.schedule?.frequency ?? "weekly",
    weekday: config.schedule?.weekday ?? "sat",
    time: config.schedule?.time ?? "02:00",
    timezone: config.schedule?.timezone ?? "UTC",
    keep_removed_days: String(config.keep_removed_days ?? 90),
    keep_history_days: String(config.keep_history_days ?? 365),
    min_pull_interval_minutes: String(config.min_pull_interval_minutes ?? 15),
    read_approval: config.read_approval ?? "every_time",
    logs_client_id: config.logs?.client_id ?? "",
    logs_workspaces: workspaceLines(config.logs),
    logs_tables: (config.logs?.tables ?? []).join(", "),
    logs_max_timespan: config.logs?.max_timespan ?? "P7D",
  };
}

const sameIds = (a: string[], b: string[]) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
const timeZones = (): string[] =>
  typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];

type Probe = { tone: "ok" | "warn" | "crit"; text: string };

export function AzureSection({ ctx }: { ctx: SettingsContext }) {
  const initial = ctx.azure;
  const toast = useToast();
  const { confirm } = useConfirm();

  const [base, setBase] = useState(() => toForm(initial?.config ?? ({} as AzureConfig)));
  const [values, setValues] = useState(base);
  const [baseIds, setBaseIds] = useState<string[]>(initial?.config.subscription_ids ?? []);
  const [ids, setIds] = useState(baseIds);
  // Always blank on load: the secret is never sent to this screen.
  const [secret, setSecret] = useState("");
  const [credential, setCredential] = useState(initial?.credential);
  const [logsCredential, setLogsCredential] = useState(initial?.logs_credential);
  const [known, setKnown] = useState<{ id: string; name: string; state: string }[]>([]);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [nextPull, setNextPull] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Only the schedule's next instant; the settings endpoint does not carry it.
  const refreshNext = useCallback(() => {
    getAzureStatus()
      .then((status) => setNextPull(status.next_pull_at))
      .catch(() => setNextPull(null));
  }, []);
  useEffect(() => {
    refreshNext();
  }, [refreshNext]);

  if (!initial || !credential) return null;

  const dirty =
    Object.entries(values).filter(([key, value]) => base[key as keyof typeof base] !== value).length +
    (sameIds(ids, baseIds) ? 0 : 1) +
    (secret ? 1 : 0);

  const set = (key: keyof typeof base) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: event.target.value }));
  };

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const config: Partial<AzureConfig> = {
        cloud: values.cloud,
        tenant_id: values.tenant_id.trim(),
        client_id: values.client_id.trim(),
        auth: values.auth as AzureConfig["auth"],
        secret_expires_on: values.secret_expires_on,
        subscriptions_mode: values.subscriptions_mode as AzureConfig["subscriptions_mode"],
        subscription_ids: ids,
        schedule: {
          frequency: values.frequency as AzureConfig["schedule"]["frequency"],
          weekday: values.weekday,
          time: values.time,
          timezone: values.timezone.trim() || "UTC",
        },
        keep_removed_days: Number(values.keep_removed_days) || 90,
        keep_history_days: Number(values.keep_history_days) || 365,
        min_pull_interval_minutes: Number(values.min_pull_interval_minutes) || 15,
        read_approval: values.read_approval as ReadApproval,
        logs: {
          client_id: values.logs_client_id.trim(),
          workspaces: parseWorkspaces(values.logs_workspaces),
          tables: parseTables(values.logs_tables),
          max_timespan: values.logs_max_timespan.trim() || "P7D",
        },
      };
      const result = await saveAzureConfig({
        config,
        ...(secret ? { secret, secret_expires_on: values.secret_expires_on } : {}),
      });
      const next = toForm(result.config);
      setBase(next);
      setValues(next);
      setBaseIds(result.config.subscription_ids ?? []);
      setIds(result.config.subscription_ids ?? []);
      setCredential(result.credential);
      setLogsCredential(result.logs_credential);
      setSecret("");
      setSaved(true);
      refreshNext();
      ctx.reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function createCertificate() {
    if (
      credential?.set &&
      !(await confirm({
        title: "Create a new certificate?",
        body:
          "This replaces the current credential. Sign-in stops working until you upload the new public file in Azure.",
        confirmLabel: "Create new certificate",
      }))
    ) {
      return;
    }
    setBusy(true);
    try {
      const result = await generateAzureCertificate();
      setCredential(result.credential);
      toast.success("Certificate created. Download the public file and upload it in Azure.");
      ctx.reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function createLogsCertificate() {
    if (
      logsCredential?.set &&
      !(await confirm({
        title: "Create a new log certificate?",
        body: "This replaces the current one. Log queries stop working until you upload the new public file in Azure.",
        confirmLabel: "Create new certificate",
      }))
    ) {
      return;
    }
    setBusy(true);
    try {
      const result = await generateAzureLogsCertificate();
      setLogsCredential(result.logs_credential);
      toast.success("Log certificate created. Download the public file and upload it in Azure.");
      ctx.reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function downloadLogsPublic() {
    try {
      const { public_certificate } = await getAzureLogsPublicCertificate();
      saveExport(
        { ok: true, filename: "rezztor-netops-azure-logs.cer", content: public_certificate },
        "application/x-x509-ca-cert",
      );
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function downloadPublic() {
    try {
      const { public_certificate } = await getAzurePublicCertificate();
      saveExport(
        { ok: true, filename: "rezztor-netops-azure.cer", content: public_certificate },
        "application/x-x509-ca-cert",
      );
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function testConnection() {
    setBusy(true);
    setProbe(null);
    try {
      const result = await testAzureConnection();
      if (!result.ok && result.signed_in === undefined) {
        setProbe({ tone: "crit", text: result.error || "The test could not run." });
      } else if (!result.signed_in) {
        setProbe({ tone: "crit", text: SIGN_IN_FAILURES[result.reason ?? "other"] ?? result.message });
      } else if (result.subscriptions.length === 0) {
        setProbe({
          tone: "warn",
          text: "Signed in, but no subscription is readable. Give the app the Reader role (step 5 of the setup).",
        });
      } else {
        setKnown(result.subscriptions);
        setProbe({
          tone: "ok",
          text: `Signed in. ${result.subscriptions.map((s) => s.name).join(", ")}. ${result.resources.toLocaleString()} resources visible.`,
        });
      }
    } catch (caught) {
      setProbe({ tone: "crit", text: caught instanceof Error ? caught.message : String(caught) });
    } finally {
      setBusy(false);
    }
  }

  const toggleId = (id: string, on: boolean) => {
    setSaved(false);
    setIds((current) => (on ? [...current, id] : current.filter((x) => x !== id)));
  };
  const listed = [
    ...known,
    ...ids.filter((id) => !known.some((s) => s.id === id)).map((id) => ({ id, name: id, state: "" })),
  ];
  const useCertificate = values.auth === "certificate";

  return (
    <>
      <Card
        title="Azure"
        description="Read-only connection to one Azure tenant. It can look at resources and cannot change them. Changing anything here needs “manage plugins”."
        actions={
          credential.set && values.tenant_id ? <Tag tone="ok">credential set</Tag> : <Tag tone="muted">not set up</Tag>
        }
      >
        <Row label="Azure cloud" help="Which Microsoft addresses are used. The addresses are fixed in the code.">
          <Select value={values.cloud} onChange={set("cloud")} disabled={saving}>
            <option value="public">Azure public</option>
          </Select>
        </Row>
        <Row label="Directory (tenant) ID" help="An identifier, not a secret.">
          <TextInput value={values.tenant_id} onChange={set("tenant_id")} autoComplete="off" disabled={saving} />
        </Row>
        <Row label="Application (client) ID" help="An identifier, not a secret.">
          <TextInput value={values.client_id} onChange={set("client_id")} autoComplete="off" disabled={saving} />
        </Row>
      </Card>

      <Card title="Credential" description="Write-only: after saving, only the kind, a thumbprint and the expiry date are shown.">
        <Row label="Kind">
          <Select value={values.auth} onChange={set("auth")} disabled={saving} aria-label="Credential kind">
            <option value="certificate">Certificate made here</option>
            <option value="secret">Client secret</option>
          </Select>
        </Row>
        {useCertificate ? (
          <Row
            label="Certificate"
            help={
              credential.kind === "certificate" && credential.set
                ? "The private key is never shown and cannot be downloaded. To replace it, create a new one and upload its public file in Azure."
                : "Create one, download the public file (.cer) and upload it under the app's Certificates & secrets in Azure."
            }
          >
            <div data-testid="azure-certificate">
              {credential.kind === "certificate" && credential.set ? (
                <p>
                  Certificate set · thumbprint <code>{credential.fingerprint}</code> · expires{" "}
                  <DateTime value={credential.expires_on} mode="date" />
                </p>
              ) : (
                <p>No certificate yet.</p>
              )}
              <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
                <Button disabled={busy || saving} onClick={() => void createCertificate()}>
                  {credential.kind === "certificate" && credential.set ? "Create new certificate" : "Create certificate"}
                </Button>
                {credential.kind === "certificate" && credential.set && (
                  <Button disabled={busy} onClick={() => void downloadPublic()}>
                    Download public file (.cer)
                  </Button>
                )}
              </div>
            </div>
          </Row>
        ) : (
          <>
            <Row
              label="Client secret"
              help={
                credential.kind === "secret" && credential.set
                  ? "Already set. Leave blank to keep it; type a new one to replace it."
                  : "Not set yet. Microsoft advises a certificate over a secret."
              }
            >
              <TextInput
                type="password"
                aria-label="Client secret"
                value={secret}
                onChange={(event) => {
                  setSaved(false);
                  setSecret(event.target.value);
                }}
                autoComplete="new-password"
                placeholder={credential.kind === "secret" && credential.set ? "••••••••" : ""}
                disabled={saving}
              />
            </Row>
            <Row label="Secret expires on" help="Typed from the Azure portal. The Sources card warns 30 days before.">
              <TextInput type="date" value={values.secret_expires_on} onChange={set("secret_expires_on")} disabled={saving} />
            </Row>
          </>
        )}
        <Row
          label="Test connection"
          help="Signs in, lists the readable subscriptions and counts the visible resources. Stores nothing. Uses the saved settings."
        >
          <div>
            <Button disabled={busy || saving || dirty > 0} onClick={() => void testConnection()}>
              Test connection
            </Button>
            {dirty > 0 && <p>Save your changes first.</p>}
            {probe && (
              <p role="status" data-testid="azure-probe" data-tone={probe.tone} style={{ marginTop: "var(--space-2)" }}>
                {probe.text}
              </p>
            )}
          </div>
        </Row>
      </Card>

      <Card title="What to collect, and when">
        <Row label="Subscriptions" help="The list is filled in by Test connection.">
          <div>
            <Select value={values.subscriptions_mode} onChange={set("subscriptions_mode")} disabled={saving} aria-label="Subscriptions">
              <option value="all">All the account can read</option>
              <option value="only">Only these</option>
              <option value="except">All except these</option>
            </Select>
            {values.subscriptions_mode !== "all" && (
              <div data-testid="azure-subscriptions">
                {listed.length === 0 && <p>Press Test connection to list the subscriptions.</p>}
                {listed.map((sub) => (
                  <label key={sub.id} style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={ids.includes(sub.id)}
                      onChange={(event) => toggleId(sub.id, event.target.checked)}
                      disabled={saving}
                    />
                    {sub.name}
                    {sub.state && sub.state !== "Enabled" ? ` (${sub.state})` : ""}
                  </label>
                ))}
              </div>
            )}
          </div>
        </Row>
        <Row label="Pull the inventory" help="Off, every day or every week. A pull missed because the server was off runs once when it comes back.">
          <Select value={values.frequency} onChange={set("frequency")} disabled={saving} aria-label="Pull the inventory">
            <option value="off">Off</option>
            <option value="daily">Every day</option>
            <option value="weekly">Every week</option>
          </Select>
        </Row>
        {values.frequency === "weekly" && (
          <Row label="Day">
            <Select value={values.weekday} onChange={set("weekday")} disabled={saving} aria-label="Day">
              {WEEKDAYS.map((day) => (
                <option key={day} value={day}>
                  {WEEKDAY_NAMES[day]}
                </option>
              ))}
            </Select>
          </Row>
        )}
        {values.frequency !== "off" && (
          <>
            <Row label="Time">
              <TextInput type="time" value={values.time} onChange={set("time")} disabled={saving} aria-label="Time" />
            </Row>
            <Row label="Time zone" help="A name such as Asia/Dubai or UTC.">
              <TextInput
                value={values.timezone}
                onChange={set("timezone")}
                list="azure-time-zones"
                disabled={saving}
                aria-label="Time zone"
              />
              <datalist id="azure-time-zones">
                {timeZones().map((zone) => (
                  <option key={zone} value={zone} />
                ))}
              </datalist>
            </Row>
          </>
        )}
        <Row label="Next pull">
          <span data-testid="azure-next-pull">
            {values.frequency === "off" ? "Off" : nextPull ? <DateTime value={nextPull} /> : "—"}
          </span>
        </Row>
        <Row label="Keep removed resources" help="Days a resource Azure no longer reports stays visible, marked removed.">
          <TextInput value={values.keep_removed_days} onChange={set("keep_removed_days")} inputMode="numeric" disabled={saving} />
        </Row>
        <Row label="Keep change history" help="Days the added, changed and removed lines of old pulls are kept.">
          <TextInput value={values.keep_history_days} onChange={set("keep_history_days")} inputMode="numeric" disabled={saving} />
        </Row>
        <Row label="Shortest gap between pulls" help="Minutes. A Pull now is refused if another pull finished more recently.">
          <TextInput
            value={values.min_pull_interval_minutes}
            onChange={set("min_pull_interval_minutes")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
      </Card>

      <Card
        title="Live reads"
        description="Asking Azure about one resource right now (azure_read). It never changes anything. A free-form log query always asks, whatever this says."
      >
        <Row
          label="Approval"
          help="Only an engineer who may approve can have this replace the card. Anything else asks every time."
        >
          <Select value={values.read_approval} onChange={set("read_approval")} disabled={saving} aria-label="Live read approval">
            {READ_APPROVALS.map((mode) => (
              <option key={mode} value={mode}>
                {READ_APPROVAL_TEXT[mode]}
              </option>
            ))}
          </Select>
        </Row>
      </Card>

      <Card
        title="Log queries"
        description="Reads from Log Analytics through a second app registration that holds only the Log Analytics Reader role on the workspaces below. Off until all of its fields are set."
        actions={
          logsCredential?.set && values.logs_client_id ? <Tag tone="ok">log app set</Tag> : <Tag tone="muted">not set up</Tag>
        }
      >
        <Row label="Log application (client) ID" help="An identifier, not a secret. A different app from the one above.">
          <TextInput
            value={values.logs_client_id}
            onChange={set("logs_client_id")}
            autoComplete="off"
            disabled={saving}
            aria-label="Log application ID"
          />
        </Row>
        <Row
          label="Log certificate"
          help="Create one, download the public file (.cer) and upload it under the log app's Certificates & secrets. The private key is never shown."
        >
          <div data-testid="azure-logs-certificate">
            {logsCredential?.set ? (
              <p>
                Certificate set · thumbprint <code>{logsCredential.fingerprint}</code> · expires{" "}
                <DateTime value={logsCredential.expires_on} mode="date" />
              </p>
            ) : (
              <p>No log certificate yet.</p>
            )}
            <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
              <Button disabled={busy || saving} onClick={() => void createLogsCertificate()}>
                {logsCredential?.set ? "Create new log certificate" : "Create log certificate"}
              </Button>
              {logsCredential?.set && (
                <Button disabled={busy} onClick={() => void downloadLogsPublic()}>
                  Download log public file (.cer)
                </Button>
              )}
            </div>
          </div>
        </Row>
        <Row
          label="Workspaces"
          help="One per line: a short name, then the workspace ID. Only these can be queried; a query names one by its short name."
        >
          <textarea
            value={values.logs_workspaces}
            onChange={set("logs_workspaces")}
            rows={3}
            spellCheck={false}
            disabled={saving}
            aria-label="Log workspaces"
            placeholder="prod 00000000-0000-0000-0000-000000000000"
            style={{ width: "100%", fontFamily: "var(--font-mono, monospace)" }}
          />
        </Row>
        <Row
          label="Tables a query may read"
          help="Separated by commas. Leave identity tables (SigninLogs, AuditLogs, SecurityEvent) out. An empty list means nothing can be queried."
        >
          <TextInput value={values.logs_tables} onChange={set("logs_tables")} disabled={saving} aria-label="Log tables" />
        </Row>
        <Row label="Longest time range" help="One query may look back this far, for example P7D (7 days). From PT1H to P30D.">
          <TextInput
            value={values.logs_max_timespan}
            onChange={set("logs_max_timespan")}
            disabled={saving}
            aria-label="Longest log time range"
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
          setIds(baseIds);
          setSecret("");
          setError(null);
        }}
      />
    </>
  );
}
