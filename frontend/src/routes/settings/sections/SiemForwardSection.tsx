/**
 * Forwarding the audit trail to a SIEM, as CEF over UDP syslog.
 *
 * Takes itself off the screen entirely when its plugin is switched off in the
 * Plugins screen -- the behaviour asked for when it was specified, expressed
 * here as the registry's `visible` predicate rather than a `return null` buried
 * in the component.
 */

import { useState } from "react";

import {
  resetSiemForwardStats,
  saveSiemForwardConfig,
  sendSiemForwardTest,
  type SiemForwardState,
} from "@/api/siemForward";
import { Button, Card, Mono, Row, ScopeNote, Select, TextInput, Toggle } from "@/components";
import { SaveBar } from "../parts";
import type { SettingsContext } from "../types";

function toSiemValues(config: SiemForwardState["config"] | undefined) {
  return {
    host: config?.host ?? "",
    port: String(config?.port ?? 514),
    syslog_format: config?.syslog_format ?? "rfc3164",
    facility: String(config?.facility ?? 16),
    device_vendor: config?.device_vendor ?? "Rezztor",
    device_product: config?.device_product ?? "EAgent",
    min_severity: String(config?.min_severity ?? 0),
    queue_size: String(config?.queue_size ?? 1000),
  };
}

/**
 * Reject out-of-range numbers before the request, in the field's own terms.
 *
 * The backend clamps or rejects these, but a form that lets you save 70000 as a
 * port and then reports a generic failure is worse than one that says which
 * field is wrong while you are still looking at it.
 */
function validate(values: ReturnType<typeof toSiemValues>): string | null {
  const port = Number(values.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return "Port must be between 1 and 65535.";
  const facility = Number(values.facility);
  if (!Number.isInteger(facility) || facility < 0 || facility > 23) return "Facility must be between 0 and 23.";
  const severity = Number(values.min_severity);
  if (!Number.isInteger(severity) || severity < 0 || severity > 10) return "Minimum severity must be between 0 and 10.";
  const queue = Number(values.queue_size);
  if (!Number.isInteger(queue) || queue < 1) return "Queue size must be a positive whole number.";
  return null;
}

export function SiemForwardSection({ ctx }: { ctx: SettingsContext }) {
  const initial = ctx.siem;

  const [base, setBase] = useState(() => toSiemValues(initial?.config));
  const [values, setValues] = useState(base);
  const [baseEnabled, setBaseEnabled] = useState(Boolean(initial?.config.enabled));
  const [enabled, setEnabled] = useState(Boolean(initial?.config.enabled));
  const [stats, setStats] = useState(initial?.stats);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  if (!initial || !stats) return null;

  const dirty =
    Object.entries(values).filter(([key, value]) => base[key as keyof typeof base] !== value)
      .length + (enabled !== baseEnabled ? 1 : 0);

  async function save() {
    const invalid = validate(values);
    if (invalid) {
      setError(invalid);
      setSaved(false);
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const result = await saveSiemForwardConfig({
        enabled,
        host: values.host,
        port: Number(values.port) || 514,
        syslog_format: values.syslog_format,
        facility: Number(values.facility) || 16,
        device_vendor: values.device_vendor,
        device_product: values.device_product,
        min_severity: Number(values.min_severity) || 0,
        queue_size: Number(values.queue_size) || 1000,
      });
      if (result.config) {
        const next = toSiemValues(result.config);
        setBase(next);
        setValues(next);
        setBaseEnabled(Boolean(result.config.enabled));
        setEnabled(Boolean(result.config.enabled));
      }
      if (result.stats) setStats(result.stats);
      setSaved(true);
      ctx.reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTestResult(null);
    setError(null);
    // Said rather than shown as a dead button. The Host field is several rows
    // above this one, so on a short window the control and the field that
    // gates it are not on screen together.
    if (!values.host) {
      setError("Set a collector host before sending a test event.");
      return;
    }
    const result = await sendSiemForwardTest();
    setTestResult(
      result.ok
        ? { ok: true, text: `Sent. ${result.line ?? ""}` }
        : { ok: false, text: result.error ?? "Could not send the test event." },
    );
  }

  const set = (key: keyof typeof base) => (event: React.ChangeEvent<HTMLInputElement>) => {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: event.target.value }));
  };

  return (
    <>
      <ScopeNote>
        <div>
          <strong>This is a convenience channel, not the audit record.</strong> UDP cannot
          confirm delivery, and events are formatted and sent by this application — so it
          can only be as trustworthy as the application itself. A deployment that needs a
          record it can rely on during an incident should ship this container's stdout to
          the SIEM instead; every audit event is already written there.
        </div>
      </ScopeNote>

      <Card
        title="SIEM forwarding"
        description="Sends a copy of this instance's audit trail to a Syslog server or SIEM collector, as CEF over UDP."
      >
        <Row label="Forward audit events" help="Off leaves the plugin installed but sending nothing.">
          <Toggle
            checked={enabled}
            onChange={(next) => {
              setSaved(false);
              setEnabled(next);
            }}
            label="Forward audit events"
            disabled={saving}
          />
        </Row>
        <Row label="Collector host" help="Hostname or IP of the Syslog server or SIEM collector.">
          <TextInput
            value={values.host}
            onChange={set("host")}
            placeholder="siem.example.com"
            disabled={saving}
          />
        </Row>
        <Row label="Port" help="514 is the syslog default.">
          <TextInput
            value={values.port}
            onChange={set("port")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
        <Row
          label="Syslog format"
          help="RFC 3164 is accepted by almost every collector. RFC 5424 is the structured successor."
        >
          <Select
            value={values.syslog_format}
            onChange={(event) => {
              setSaved(false);
              setValues((c) => ({ ...c, syslog_format: event.target.value }));
            }}
            disabled={saving}
          >
            {initial.syslog_formats.map((format) => (
              <option key={format} value={format}>
                {format}
              </option>
            ))}
          </Select>
        </Row>
        <Row
          label="Facility"
          help="Syslog facility number, 0–23. 16 is local0, the usual choice for application events."
        >
          <TextInput
            value={values.facility}
            onChange={set("facility")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
        <Row
          label="Minimum severity"
          help="CEF severity 0–10 below which events are not sent. 0 forwards everything. A failed sign-in is 8."
        >
          <TextInput
            value={values.min_severity}
            onChange={set("min_severity")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
        <Row
          label="Queue size"
          help="How many events buffer in memory while the collector is slow. When it fills, the oldest are dropped rather than blocking the audit write — that is what 'dropped' below counts."
        >
          <TextInput
            value={values.queue_size}
            onChange={set("queue_size")}
            inputMode="numeric"
            disabled={saving}
          />
        </Row>
        <Row label="Device vendor" help="The CEF header's vendor field, as the SIEM will show it.">
          <TextInput value={values.device_vendor} onChange={set("device_vendor")} disabled={saving} />
        </Row>
        <Row
          label="Device product"
          help={`Product field. Version is read live: ${initial.device_version}.`}
        >
          <TextInput
            value={values.device_product}
            onChange={set("device_product")}
            disabled={saving}
          />
        </Row>

        <Row
          label="Send a test event"
          help="Sends one synthetic CEF event now. Success means the packet left this host — over UDP nothing can confirm the collector received or parsed it."
        >
          <Button disabled={saving} onClick={() => void test()}>
            Send test event
          </Button>
        </Row>
        {testResult && (
          <p
            style={{
              padding: "var(--space-2) 0",
              fontSize: "var(--text-xs)",
              color: testResult.ok ? "var(--ok)" : "var(--crit)",
              wordBreak: "break-all",
            }}
          >
            <Mono>{testResult.text}</Mono>
          </p>
        )}

        <Row
          label="Since last reset"
          help="Dropped events mean the queue filled faster than the collector could be written to — the oldest were discarded rather than blocking the audit write."
        >
          <span style={{ fontSize: "var(--text-sm)", color: "var(--ink-2)" }}>
            <Mono>{stats.sent} sent</Mono>
            {" · "}
            <span style={{ color: stats.dropped_queue_full ? "var(--warn)" : undefined }}>
              <Mono>{stats.dropped_queue_full} dropped</Mono>
            </span>
            {" · "}
            <span style={{ color: stats.send_errors ? "var(--crit)" : undefined }}>
              <Mono>{stats.send_errors} errors</Mono>
            </span>
            {"  "}
            <Button
              onClick={async () => {
                const result = await resetSiemForwardStats();
                if (result.stats) setStats(result.stats);
                ctx.reload();
              }}
            >
              Reset
            </Button>
          </span>
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
          setEnabled(baseEnabled);
          setError(null);
        }}
      />
    </>
  );
}
