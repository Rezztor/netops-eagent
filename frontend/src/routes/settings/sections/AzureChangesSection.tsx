/**
 * What the product may change in Azure, and who it acts as: the guard rails, the CR or SR number's format, the two keys
 * and the role test. `plugins.manage`, through the change plugin's own endpoint, which proxies to the cloud executor.
 *
 * The executor stores and enforces the guard rails, so a compromised application cannot widen them. There is no row for
 * deleting, stopping, restarting, resizing or scaling in, and that is deliberate: none of it is configurable (ADR 0065).
 * A key's private half never leaves the executor; making one returns the public file once, to upload in Azure.
 */

import { useState } from "react";

import {
  getGuardrails,
  getKeys,
  makeKey,
  saveGuardrails,
  testRole,
  type Guardrails,
  type KeyName,
  type KeysState,
  type RoleTest,
} from "@/api/azureChange";
import { Button, Card, CenteredMessage, DateTime, Mono, Row, Tag, TextInput, Toggle, useConfirm, useToast } from "@/components";
import { saveExport } from "@/routes/inventory/download";
import { useAsync } from "@/lib/useAsync";
import { matchesReference, patternProblem } from "@/lib/referencePattern";
import { SaveBar } from "../parts";
import type { SettingsContext } from "../types";

const list = (text: string) => text.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

function toForm(rails: Guardrails) {
  return {
    subscriptions: rails.subscriptions.join(", "),
    existing: rails.resource_groups.existing.join(", "),
    new_prefix: rails.resource_groups.new_prefix,
    regions: rails.regions.join(", "),
    resource_types: rails.resource_types.join(", "),
    vm_sizes_max_vcpu: String(rails.vm_sizes_max_vcpu),
    usd_month_per_change: String(rails.usd_month_per_change),
    usd_month_total: String(rails.usd_month_total),
    low_risk_usd_month: String(rails.low_risk_usd_month),
    max_resources: String(rails.max_resources),
    allow_unknown_price: rails.allow_unknown_price,
    allow_free_code: rails.allow_free_code,
    reference_pattern: rails.reference_pattern,
    document_valid_days: String(rails.document_valid_days),
  };
}
type Form = ReturnType<typeof toForm>;

/** The first thing wrong with the form, in the field's own terms, or null. Exported for the test. */
export function formProblem(form: Form): string | null {
  const whole = (value: string, min: number, max: number, name: string) => {
    const n = Number(value);
    return Number.isInteger(n) && n >= min && n <= max ? null : `${name} must be a whole number from ${min} to ${max}.`;
  };
  const money = (value: string, name: string) =>
    value.trim() !== "" && Number(value) >= 0 ? null : `${name} must be a number, zero or more.`;
  return (
    patternProblem(form.reference_pattern) ||
    whole(form.document_valid_days, 1, 30, "Days a document may wait") ||
    whole(form.vm_sizes_max_vcpu, 1, 128, "Largest VM (vCPU)") ||
    whole(form.max_resources, 1, 500, "Most resources in one change") ||
    money(form.usd_month_per_change, "The ceiling for one change") ||
    money(form.usd_month_total, "The ceiling across approved changes") ||
    money(form.low_risk_usd_month, "The low-risk limit")
  );
}

function fromForm(form: Form): Guardrails {
  return {
    subscriptions: list(form.subscriptions),
    resource_groups: { existing: list(form.existing), new_prefix: form.new_prefix.trim() },
    regions: list(form.regions),
    resource_types: list(form.resource_types),
    vm_sizes_max_vcpu: Number(form.vm_sizes_max_vcpu),
    usd_month_per_change: Number(form.usd_month_per_change),
    usd_month_total: Number(form.usd_month_total),
    low_risk_usd_month: Number(form.low_risk_usd_month),
    max_resources: Number(form.max_resources),
    allow_free_code: form.allow_free_code,
    allow_unknown_price: form.allow_unknown_price,
    reference_pattern: form.reference_pattern.trim(),
    document_valid_days: Number(form.document_valid_days),
  };
}

export function AzureChangesSection({ ctx }: { ctx: SettingsContext }) {
  const rails = useAsync(getGuardrails, []);
  if (rails.state.status === "loading") return <CenteredMessage title="Loading the guard rails…" />;
  if (rails.state.status === "error") {
    return (
      <CenteredMessage
        title="Azure changes are not available"
        detail={`${rails.state.error.message} Check that the Azure changes plugin is on (Plugins) and that the cloud executor is running.`}
        action={<Button onClick={rails.reload}>Retry</Button>}
      />
    );
  }
  return (
    <>
      <Rails initial={rails.state.data} />
      <Keys ctx={ctx} />
    </>
  );
}

function Rails({ initial }: { initial: Guardrails }) {
  const [base, setBase] = useState(() => toForm(initial));
  const [values, setValues] = useState(base);
  const [sample, setSample] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const dirty = (Object.keys(values) as (keyof Form)[]).filter((key) => values[key] !== base[key]).length;
  const set = (key: keyof Form) => (event: React.ChangeEvent<HTMLInputElement>) => {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: event.target.value }));
  };
  const flip = (key: "allow_unknown_price" | "allow_free_code") => (next: boolean) => {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: next }));
  };
  const disabled = saving;
  const patternError = patternProblem(values.reference_pattern);

  async function save() {
    const problem = formProblem(values);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const next = toForm(await saveGuardrails(fromForm(values)));
      setBase(next);
      setValues(next);
      setSaved(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Card
        title="Azure changes"
        description="What the assistant may add or change in Azure, after an engineer approves it with a CR or SR number. It never deletes, stops, restarts, resizes or scales in: that is not a setting. The cloud executor stores and enforces these rules."
      >
        <Row label="Allowed subscriptions" help="Subscription IDs, separated by commas. Empty: nothing can change.">
          <TextInput value={values.subscriptions} onChange={set("subscriptions")} autoComplete="off" disabled={disabled} aria-label="Allowed subscriptions" />
        </Row>
        <Row label="Allowed resource groups" help="Existing groups it may add to, separated by commas.">
          <TextInput value={values.existing} onChange={set("existing")} autoComplete="off" disabled={disabled} aria-label="Allowed resource groups" />
        </Row>
        <Row label="New resource groups start with" help="A new group may be made only if its name starts with this.">
          <TextInput value={values.new_prefix} onChange={set("new_prefix")} autoComplete="off" disabled={disabled} aria-label="New resource group prefix" />
        </Row>
        <Row label="Allowed regions" help="Separated by commas. Empty: the regions already in your inventory.">
          <TextInput value={values.regions} onChange={set("regions")} autoComplete="off" disabled={disabled} aria-label="Allowed regions" />
        </Row>
        <Row label="Allowed resource types" help="Separated by commas. Empty: the types the templates declare.">
          <TextInput value={values.resource_types} onChange={set("resource_types")} autoComplete="off" disabled={disabled} aria-label="Allowed resource types" />
        </Row>
        <Row label="Largest VM (vCPU)">
          <TextInput value={values.vm_sizes_max_vcpu} onChange={set("vm_sizes_max_vcpu")} inputMode="numeric" disabled={disabled} aria-label="Largest VM" />
        </Row>
        <Row label="Most resources in one change">
          <TextInput value={values.max_resources} onChange={set("max_resources")} inputMode="numeric" disabled={disabled} aria-label="Most resources in one change" />
        </Row>
      </Card>

      <Card title="Spending" description="At list price, 730 hours a month, before data transfer and your discounts.">
        <Row label="Ceiling for one change (USD a month)">
          <TextInput value={values.usd_month_per_change} onChange={set("usd_month_per_change")} inputMode="decimal" disabled={disabled} aria-label="Ceiling for one change" />
        </Row>
        <Row label="Ceiling across approved changes (USD a month)">
          <TextInput value={values.usd_month_total} onChange={set("usd_month_total")} inputMode="decimal" disabled={disabled} aria-label="Ceiling across approved changes" />
        </Row>
        <Row label="Low-risk limit (USD a month)" help="A change under this, from a reviewed template, is shown as low risk; any other is high risk.">
          <TextInput value={values.low_risk_usd_month} onChange={set("low_risk_usd_month")} inputMode="decimal" disabled={disabled} aria-label="Low-risk limit" />
        </Row>
        <Row label="Allow a change with an unknown price" help="Off: a change that cannot be priced is refused.">
          <Toggle checked={values.allow_unknown_price} onChange={flip("allow_unknown_price")} label="Allow a change with an unknown price" disabled={disabled} />
        </Row>
        <Row label="Allow free code" help="Off: only the reviewed templates may be used. Free code is always high risk.">
          <Toggle checked={values.allow_free_code} onChange={flip("allow_free_code")} label="Allow free code" disabled={disabled} />
        </Row>
      </Card>

      <Card title="The CR or SR number and the change document">
        <Row
          label="Number format"
          help="A pattern the whole number must match, ignoring case, 40 characters at most. An engineer must type a matching number to approve any Azure change."
        >
          <div>
            <TextInput value={values.reference_pattern} onChange={set("reference_pattern")} autoComplete="off" spellCheck={false} disabled={disabled} aria-label="Number format" />
            {patternError && <p role="alert" style={{ color: "var(--crit)" }}>{patternError}</p>}
          </div>
        </Row>
        <Row label="Try a number" help="Checks a sample against the pattern above. Nothing is saved or sent.">
          <div>
            <TextInput value={sample} onChange={(event) => setSample(event.target.value)} autoComplete="off" spellCheck={false} aria-label="Sample number" />
            {sample.trim() !== "" && (
              <p data-testid="reference-sample">
                {!patternError && matchesReference(values.reference_pattern, sample) ? (
                  <Tag tone="ok">matches</Tag>
                ) : (
                  <Tag tone="crit">does not match</Tag>
                )}
              </p>
            )}
          </div>
        </Row>
        <Row label="Days a document may wait" help="From 1 to 30. A change not approved by then expires and must be drafted again.">
          <TextInput value={values.document_valid_days} onChange={set("document_valid_days")} inputMode="numeric" disabled={disabled} aria-label="Days a document may wait" />
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
          setError(null);
        }}
      />
    </>
  );
}

const KEY_TITLES: Record<KeyName, { title: string; help: string }> = {
  planner: {
    title: "Planner key",
    help: "A second certificate on the read app. It lets the executor plan a change; it can only read.",
  },
  applier: {
    title: "Change app key",
    help: "The change app's own certificate. Give that app only the Rezztor Change role (docs/guides/azure-changes.md).",
  },
};

function Keys({ ctx }: { ctx: SettingsContext }) {
  const keys = useAsync(getKeys, []);
  const toast = useToast();
  const { confirm } = useConfirm();
  const [busy, setBusy] = useState(false);
  const [ids, setIds] = useState<Record<KeyName, string>>({
    planner: ctx.azure?.config.client_id ?? "",
    applier: "",
  });
  const [role, setRole] = useState<RoleTest | null>(null);
  const tenant = ctx.azure?.config.tenant_id ?? "";

  async function create(which: KeyName, current: KeysState | null) {
    const clientId = (ids[which] || current?.[which].client_id || "").trim();
    if (!tenant || !clientId) {
      toast.error("Set up the Azure connection first, and enter the application (client) ID of this app.");
      return;
    }
    if (
      current?.[which].set &&
      !(await confirm({
        title: "Make a new key?",
        body: "This replaces the current key. Changes using it stop working until you upload the new public file in Azure.",
        confirmLabel: "Make a new key",
      }))
    ) {
      return;
    }
    setBusy(true);
    try {
      const made = await makeKey(which, tenant, clientId);
      saveExport(
        { ok: true, filename: `rezztor-netops-azure-${which}.pem`, content: made.public_certificate },
        "application/x-pem-file",
      );
      toast.success("Key made. Upload the public file you just downloaded in Azure.");
      keys.reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function check() {
    setBusy(true);
    setRole(null);
    try {
      setRole(await testRole());
    } catch (caught) {
      setRole({ ok: false, error: caught instanceof Error ? caught.message : String(caught) });
    } finally {
      setBusy(false);
    }
  }

  const current = keys.state.status === "ready" ? keys.state.data : null;
  return (
    <Card
      title="Keys and role"
      description="The keys live in the cloud executor and are never shown. Making one gives you the public file to upload in Azure."
    >
      {keys.state.status === "error" && (
        <p role="alert" style={{ color: "var(--crit)" }}>{keys.state.error.message}</p>
      )}
      {(["planner", "applier"] as const).map((which) => {
        const key = current?.[which];
        return (
          <Row key={which} label={KEY_TITLES[which].title} help={KEY_TITLES[which].help}>
            <div data-testid={`key-${which}`}>
              {key?.set ? (
                <p>
                  Set · thumbprint <Mono>{key.thumbprint}</Mono>
                  {key.expires_on ? (
                    <>
                      {" "}· expires <DateTime value={key.expires_on} mode="date" />
                    </>
                  ) : null}
                </p>
              ) : (
                <p>No key yet.</p>
              )}
              <TextInput
                value={ids[which]}
                onChange={(event) => setIds((c) => ({ ...c, [which]: event.target.value }))}
                placeholder={key?.client_id || "Application (client) ID"}
                autoComplete="off"
                disabled={busy}
                aria-label={`${KEY_TITLES[which].title}: application (client) ID`}
              />
              <Button disabled={busy} onClick={() => void create(which, current)}>
                {key?.set ? "Make a new key" : "Make a key"}
              </Button>
            </div>
          </Row>
        );
      })}
      <Row label="Encryption passphrase">
        <span>{current?.passphrase_set ? <Tag tone="ok">set</Tag> : <Tag tone="warn">not set</Tag>}</span>
      </Row>
      <Row
        label="Test the role"
        help="Signs in as the planner and the change app and reads the change app's role. Changes stay off until it holds only the Rezztor Change role."
      >
        <div>
          <Button disabled={busy} onClick={() => void check()}>Test the role</Button>
          {role && (
            <p role="status" data-testid="role-result" data-tone={role.role_ok ? "ok" : "crit"}>
              {role.role_ok ? (
                <Tag tone="ok">role verified</Tag>
              ) : (
                <>
                  <Tag tone="crit">changes are off</Tag> {role.reason || role.error || "The role was not verified."}
                </>
              )}
            </p>
          )}
        </div>
      </Row>
    </Card>
  );
}
