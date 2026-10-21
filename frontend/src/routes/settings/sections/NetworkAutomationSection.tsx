/**
 * The Ansible executor, and the changes waiting for someone to read them.
 *
 * Two things on one screen because an administrator does both, but they are not
 * the same job and they are not the same permission. Configuring the executor
 * is `plugins.manage`, which the registry gates the whole section on. Approving
 * is `network.approve`, checked here against the session before that card is
 * drawn at all — a future network-approver role holds only the second.
 *
 * The approval card shows the **commands**, not a summary of them. That is the
 * entire point: an engineer approves the text a device will receive, and a card
 * that said "12 commands" would be asking for a signature on an unread page.
 */

import { useCallback, useEffect, useState } from "react";

import {
  decideChange,
  getChangeForReview,
  getChangesAwaitingApproval,
  saveNetworkConfig,
  testNetworkExecutor,
  type ChangeDetail,
  type ChangeSummary,
  type NetworkConfig,
  type NetworkState,
  type ReadApproval,
} from "@/api/networkAutomation";
import { Button, Card, Mono, Row, Select, Tag, TextInput, Toggle, useConfirm } from "@/components";
import { askToApprove } from "@/routes/approveChange";
import { SaveBar } from "../parts";
import type { SettingsContext } from "../types";

function toFormValues(config: NetworkConfig | undefined) {
  return {
    executor_url: config?.executor_url ?? "",
    job_timeout_seconds: String(config?.job_timeout_seconds ?? 300),
    max_devices_per_job: String(config?.max_devices_per_job ?? 10),
    read_approval: config?.read_approval ?? "every_time",
    require_local_model: config?.require_local_model ?? false,
  };
}

export function NetworkAutomationSection({ ctx }: { ctx: SettingsContext }) {
  const initial = ctx.network;
  const canApprove = (ctx.session?.permissions ?? []).includes("network.approve");

  const [base, setBase] = useState(() => toFormValues(initial?.config));
  const [values, setValues] = useState(base);
  // Blank on every load, deliberately: the tokens are never sent to this screen,
  // and blank on save means "keep what is stored" rather than "clear it".
  const [token, setToken] = useState("");
  const [tokenSet, setTokenSet] = useState(Boolean(initial?.config.executor_token_set));
  const [adminToken, setAdminToken] = useState("");
  const [adminTokenSet, setAdminTokenSet] = useState(Boolean(initial?.config.admin_token_set));
  // Where each token comes from. A deployment that ships an executor injects
  // both, and then there is nothing to type here -- saying so is better than a
  // blank field that invites someone to write a second copy into usr/.env,
  // which would override the injected one and survive the Secret being rotated.
  const [tokenOrigin, setTokenOrigin] = useState(initial?.config.executor_token_origin);
  const [adminTokenOrigin, setAdminTokenOrigin] = useState(initial?.config.admin_token_origin);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<NetworkState | null>(null);
  const [probing, setProbing] = useState(false);

  if (!initial) return null;

  const dirty =
    Object.entries(values).filter(([key, value]) => base[key as keyof typeof base] !== value)
      .length + (token.length > 0 ? 1 : 0) + (adminToken.length > 0 ? 1 : 0);

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const result = await saveNetworkConfig(
        {
          executor_url: values.executor_url,
          job_timeout_seconds: Number(values.job_timeout_seconds) || 300,
          max_devices_per_job: Number(values.max_devices_per_job) || 10,
          read_approval: values.read_approval,
          require_local_model: values.require_local_model,
        },
        token || undefined,
        adminToken || undefined,
      );
      const next = toFormValues(result.config);
      setBase(next);
      setValues(next);
      setToken("");
      setTokenSet(result.config.executor_token_set);
      setTokenOrigin(result.config.executor_token_origin);
      setAdminToken("");
      setAdminTokenSet(Boolean(result.config.admin_token_set));
      setAdminTokenOrigin(result.config.admin_token_origin);
      setSaved(true);
      ctx.reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setProbing(true);
    try {
      setProbe(await testNetworkExecutor());
    } catch (caught) {
      setProbe({
        ok: false,
        plugin_enabled: true,
        config: initial!.config,
        reachable: false,
        detail: caught instanceof Error ? caught.message : String(caught),
      });
    } finally {
      setProbing(false);
    }
  }

  return (
    <>
      <Card
        title="Ansible executor"
        description="The agent never connects to a device. It sends a playbook name and validated variables to this executor, which holds the inventory and the credentials."
        actions={
          <Button
            onClick={() => {
              // The address it would check is a field in this card's body, and
              // a disabled button in the header said nothing about it.
              if (!values.executor_url) {
                setError("Set the executor URL before checking reachability.");
                return;
              }
              test();
            }}
            disabled={probing}
          >
            {probing ? "Checking…" : "Check reachability"}
          </Button>
        }
      >
        <Row label="Executor address" help="Leave empty: the executor is reached over its own socket. An address is only for a development setup.">
          <TextInput
            value={values.executor_url}
            onChange={(event) => setValues({ ...values, executor_url: event.target.value })}
          />
        </Row>
        <Row
          label="Executor token"
          help={
            tokenOrigin === "environment"
              ? "Supplied by this deployment, and refreshed every restart. Typing one here would override it with a copy that outlives the next rotation."
              : tokenSet
                ? "A token is stored. Leave blank to keep it; type a new one to replace it."
                : "Not set. The executor refuses every request until it has one."
          }
        >
          <TextInput
            type="password"
            value={token}
            autoComplete="new-password"
            placeholder={
              tokenOrigin === "environment"
                ? "•••••••• from the deployment"
                : tokenSet
                  ? "•••••••• stored"
                  : "openssl rand -hex 32"
            }
            onChange={(event) => setToken(event.target.value)}
          />
        </Row>
        <Row
          label="Administration token"
          help={
            adminTokenOrigin === "environment"
              ? "Supplied by this deployment, and refreshed every restart. Typing one here would override it with a copy that outlives the next rotation."
              : adminTokenSet
                ? "A token is stored. Leave blank to keep it. This is not the job token — the executor refuses both if they match."
                : "Not set. Network → Devices cannot edit inventory or credentials until it has one."
          }
        >
          <TextInput
            type="password"
            value={adminToken}
            autoComplete="new-password"
            placeholder={
              adminTokenOrigin === "environment"
                ? "•••••••• from the deployment"
                : adminTokenSet
                  ? "•••••••• stored"
                  : "openssl rand -hex 32"
            }
            onChange={(event) => setAdminToken(event.target.value)}
          />
        </Row>
        <Row label="Job timeout" help="Seconds one playbook may run before the executor gives up.">
          <TextInput
            value={values.job_timeout_seconds}
            inputMode="numeric"
            onChange={(event) => setValues({ ...values, job_timeout_seconds: event.target.value })}
          />
        </Row>
        <Row label="Devices per job" help="An upper bound on blast radius, enforced by the executor.">
          <TextInput
            value={values.max_devices_per_job}
            inputMode="numeric"
            onChange={(event) => setValues({ ...values, max_devices_per_job: event.target.value })}
          />
        </Row>
        <Row
          label="Read approval"
          help="How often a fixed read or bundle asks for an engineer's approval card. Your own show commands always ask. Either way each read is recorded and audited."
        >
          <Select
            value={values.read_approval}
            onChange={(event) => setValues({ ...values, read_approval: event.target.value as ReadApproval })}
          >
            <option value="every_time">Every time (default)</option>
            <option value="once_per_chat">Once per chat, per device</option>
            <option value="log_only">Never ask, record only</option>
          </Select>
        </Row>
        <Row
          label="Keep device data on a local model"
          help="Refuses the network, inventory and SolarWinds tools unless the chat and utility models answer from a loopback or private address (or a bare host name). A hosted provider, or a model with no API base, is refused."
        >
          <Toggle
            checked={values.require_local_model}
            onChange={(next) => setValues({ ...values, require_local_model: next })}
            label="Keep device data on a local model"
          />
        </Row>
        {probe?.limits && (
          <Row
            label="Jobs per minute"
            help="Set on the executor, not here. It applies to everything that reaches it — the agent, the console and the shell — which is why it is not a setting on this screen."
          >
            <Mono>{probe.limits.max_jobs_per_minute ?? "—"}</Mono>
          </Row>
        )}

        {probe && (
          <Row label="Last check">
            {probe.reachable ? (
              <div>
                <Tag tone="ok">executor answered</Tag>{" "}
                <span>
                  catalogue <Mono>{probe.catalogue_commit || "unpinned"}</Mono>,{" "}
                  {probe.catalogue_entries?.length ?? 0} entries. This says nothing about whether a
                  device is reachable.
                </span>
              </div>
            ) : (
              <div>
                <Tag tone="crit">no answer</Tag> <span>{probe.detail}</span>
              </div>
            )}
          </Row>
        )}
      </Card>

      {canApprove && <ApprovalQueue />}

      <SaveBar
        dirty={dirty}
        saving={saving}
        saved={saved}
        error={error}
        onSave={save}
        onDiscard={() => {
          setValues(base);
          setToken("");
          setAdminToken("");
        }}
      />
    </>
  );
}

/** Everything waiting on a person, and the commands each one would push. Exported for vitest. */
export function ApprovalQueue() {
  const [pending, setPending] = useState<ChangeSummary[] | null>(null);
  const [open, setOpen] = useState<ChangeDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogs = useConfirm();

  const refresh = useCallback(async () => {
    const result = await getChangesAwaitingApproval();
    setPending(result.ok ? (result.changes ?? []) : []);
    if (!result.ok) setError(result.error ?? "Could not read the approval queue.");
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function inspect(id: string) {
    setError(null);
    const result = await getChangeForReview(id);
    if (result.ok && result.change) setOpen(result.change);
    else setError(result.error ?? "Could not read that change.");
  }

  async function decide(id: string, decision: "approve" | "reject", expectedHash = "", confirmation = "") {
    setBusy(true);
    setError(null);
    try {
      await decideChange(id, decision, expectedHash, confirmation);
      setOpen(null);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  // Read again before approving: the dialog shows, and the approval binds to, the plan as it stands now, and a
  // change-high plan asks for its typed confirmation (the same dialog as the Changes tab and the Approvals page).
  async function approve(id: string) {
    setError(null);
    const result = await getChangeForReview(id);
    if (!result.ok || !result.change) {
      setError(result.error ?? "Could not read that change.");
      return;
    }
    setOpen(result.change);
    const typed = await askToApprove(dialogs, { title: `Approve "${result.change.playbook_name}"?`, plan: result.change });
    if (typed !== null) await decide(id, "approve", result.change.current_hash, typed);
  }

  return (
    <Card
      title="Changes awaiting approval"
      description="Approving means agreeing to the exact commands below. If the devices change afterwards, the change is refused as superseded rather than applied."
      actions={<Button onClick={() => void refresh()}>Refresh</Button>}
    >
      {error && (
        <Row label="Problem">
          <span style={{ color: "var(--crit)" }}>{error}</span>
        </Row>
      )}

      {pending === null && <Row label="Loading…"> </Row>}

      {pending?.length === 0 && (
        <Row label="Nothing waiting" help="Changes appear here once the agent has drafted one.">
          <Tag tone="muted">empty</Tag>
        </Row>
      )}

      {pending?.map((change) => (
        <Row
          key={change.id}
          label={<Mono>{change.playbook_name}</Mono>}
          help={`${change.command_count} commands on ${change.limit} — requested by ${change.requested_by || "unknown"}`}
        >
          <Button onClick={() => void inspect(change.id)}>
            {open?.id === change.id ? "Reading" : "Read the commands"}
          </Button>
        </Row>
      ))}

      {open && (
        <div style={{ marginTop: "var(--space-4)" }}>
          <Row
            label="Commands this would push"
            help={
              open.render_method === "reviewed_cli"
                ? "Reviewed CLI, written in advance. NOT computed against the devices, so it does not reflect their current state."
                : "Computed against the devices in check mode."
            }
          >
            {open.render_method === "reviewed_cli" ? (
              <Tag tone="warn">not computed from the devices</Tag>
            ) : (
              <Tag tone="ok">from check mode</Tag>
            )}
          </Row>
          <pre
            style={{
              background: "var(--surface-alt)",
              border: "1px solid var(--line)",
              borderRadius: "var(--radius-sm)",
              padding: "var(--space-3)",
              overflowX: "auto",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--text-sm)",
            }}
          >
            {open.commands.join("\n")}
          </pre>

          {open.rollback_commands.length > 0 && (
            <>
              <Row label="Rollback" help={`From ${open.rollback_name}. Approved at the same moment.`} />
              <pre
                style={{
                  background: "var(--surface-alt)",
                  border: "1px solid var(--line)",
                  borderRadius: "var(--radius-sm)",
                  padding: "var(--space-3)",
                  overflowX: "auto",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-sm)",
                }}
              >
                {open.rollback_commands.join("\n")}
              </pre>
            </>
          )}

          <Row label="Decision" help="If you requested this change, approving makes you accountable for what runs.">
            <div style={{ display: "flex", gap: "var(--space-2)" }}>
              <Button variant="primary" disabled={busy} onClick={() => void approve(open.id)}>
                Approve these commands
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => void decide(open.id, "reject")}>
                Reject
              </Button>
              <Button onClick={() => setOpen(null)} disabled={busy}>
                Close
              </Button>
            </div>
          </Row>
        </div>
      )}
    </Card>
  );
}
