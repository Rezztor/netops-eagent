/**
 * The network console.
 *
 * Four things an engineer needs and could previously only get by opening a shell
 * on the executor: what ran, what is waiting for approval, which devices exist,
 * and what the container actually contains.
 *
 * Two permissions run through this screen and the difference is deliberate.
 * `network.view` reads history and the catalogue — an engineer who needs to know
 * what was pushed to a switch last Tuesday should not need administrator rights
 * to find out. `plugins.manage` edits the inventory and the credentials, because
 * choosing which devices the agent can reach is the same class of decision as
 * choosing which executor it talks to. `network.approve` is a third, and the tab
 * that uses it is hidden from everyone else rather than shown and refused.
 *
 * On the limits of "EAgent maintains the executor": it maintains the executor's
 * *content* — the inventory, the credentials, and the catalogue baked into its
 * image. It does not start, stop or restart the container, and the Health tab
 * says so and prints the command instead. Nothing in this application holds a
 * Docker socket, and that is the property being preserved rather than an
 * omission.
 */

import { useCallback, useMemo, useState } from "react";

import {
  decideChange,
  getChangeForReview,
  getChangesAwaitingApproval,
  testNetworkExecutor,
  type ChangeDetail,
  type ChangeSummary,
  type NetworkState,
} from "@/api/networkAutomation";
import {
  getJob,
  listJobs,
  type JobDetail,
  type JobSummary,
} from "@/api/networkConsole";
import { PERMISSIONS } from "@/api/types";
import {
  Button,
  Card,
  CenteredMessage,
  DateTime,
  LoadFailed,
  Mono,
  Pagination,
  ScopeNote,
  SearchInput,
  Table,
  Tabs,
  Tag,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useUrlState } from "@/lib/useUrlState";
import { useSession } from "@/lib/session";
import { askToApprove, SELF_APPROVAL_COPY } from "./approveChange";
import { AzureChanges } from "./AzureChanges";
import { InventoryPanel } from "./NetworkInventory";
import { NetworksPanel } from "./NetworksPanel";
import shell from "./shell.module.css";
import { ExecutorRefused } from "./ExecutorState";
import styles from "./network.module.css";

type TabId = "jobs" | "networks" | "changes" | "devices" | "health";

const JOBS_PAGE_SIZE = 25;

function statusTone(status: string): "ok" | "warn" | "crit" | "muted" {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "crit";
  if (status === "running") return "warn";
  return "muted";
}

function when(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString();
}

function took(job: { started_at: number; finished_at: number | null }): string {
  if (!job.finished_at) return "—";
  const ms = (job.finished_at - job.started_at) * 1000;
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

export function NetworkScreen() {
  const session = useSession();
  const held = useMemo(() => new Set(session.permissions), [session.permissions]);
  const canApprove = held.has(PERMISSIONS.networkApprove);
  const canManage = held.has(PERMISSIONS.pluginsManage);

  const tabs: { id: TabId; label: string }[] = [
    { id: "jobs", label: "Jobs" },
    { id: "networks", label: "Networks" },
    ...(canApprove ? [{ id: "changes" as TabId, label: "Changes" }] : []),
    ...(canManage ? [{ id: "devices" as TabId, label: "Devices" }] : []),
    { id: "health", label: "Health" },
  ];
  // The active tab lives in the URL so a reload or a link lands on the same one.
  const [rawTab, setTab] = useUrlState("tab", "jobs");
  const tab = (tabs.some((t) => t.id === rawTab) ? rawTab : "jobs") as TabId;

  return (
    <div className={shell.stack}>
      <Tabs label="Network console" items={tabs} active={tab} onChange={setTab} />

      {tab === "jobs" && <JobsPanel />}
      {tab === "networks" && <NetworksPanel />}
      {tab === "changes" && canApprove && (
        <>
          <ChangesPanel />
          <AzureChanges />
        </>
      )}
      {tab === "devices" && canManage && <InventoryPanel />}
      {tab === "health" && <HealthPanel />}
    </div>
  );
}

/* ---------------------------------- jobs --------------------------------- */

function JobsPanel() {
  const toast = useToast();
  const [status, setStatus] = useState("");
  const [playbook, setPlaybook] = useState("");
  const [source, setSource] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<JobDetail | null>(null);

  const load = useCallback(
    () =>
      listJobs({
        limit: JOBS_PAGE_SIZE,
        offset: page * JOBS_PAGE_SIZE,
        status,
        ...(playbook.trim() ? { playbook: playbook.trim() } : {}),
        ...(source.trim() ? { source: source.trim() } : {}),
      }),
    [status, playbook, source, page],
  );
  const { state, reload } = useAsync(load, [load]);

  const open = async (jobId: string) => {
    try {
      const result = await getJob(jobId);
      if (result.ok && result.job) setSelected(result.job);
      else throw new Error(result.error || "The executor did not return that job.");
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  };

  // Any filter change resets to the first page; a stale offset can land past
  // the end of a newly-narrowed result.
  function setFilter(next: () => void) {
    next();
    setPage(0);
  }

  if (state.status === "loading") return <CenteredMessage title="Loading job history…" />;
  if (state.status === "error") {
    return (
      <CenteredMessage
        title="Could not reach the executor"
        detail={state.error.message}
        action={<Button variant="primary" onClick={reload}>Retry</Button>}
      />
    );
  }
  if (!state.data.ok) {
    return (
      <ExecutorRefused error={state.data.error} onRetry={reload} />
    );
  }

  const jobs = state.data.jobs ?? [];

  return (
    <>
      <ScopeNote>
        <div>
          This history belongs to the executor, not to this application — the
          executor is the workload that talks to devices, so its record is the one
          about the estate. It is a different record from{" "}
          <Mono>Activity</Mono>, which says who authorised something; this says
          what the device reported.
        </div>
      </ScopeNote>

      <Card
        title="Jobs"
        description={`${state.data.total ?? jobs.length} recorded`}
        actions={
          <>
            {["", "succeeded", "failed", "running", "queued"].map((value) => (
              <Button
                key={value || "all"}
                variant={status === value ? "primary" : "default"}
                onClick={() => setFilter(() => setStatus(value))}
              >
                {value || "All"}
              </Button>
            ))}
            <Button onClick={reload}>Refresh</Button>
          </>
        }
      >
        <div
          style={{
            display: "flex",
            gap: "var(--space-2)",
            flexWrap: "wrap",
            alignItems: "center",
            padding: "var(--space-3) 0",
          }}
        >
          <SearchInput
            value={playbook}
            onChange={(value) => setFilter(() => setPlaybook(value))}
            placeholder="Filter by playbook…"
            label="Filter by playbook"
          />
          <SearchInput
            value={source}
            onChange={(value) => setFilter(() => setSource(value))}
            placeholder="Filter by source…"
            label="Filter by source"
          />
        </div>
        {jobs.length ? (
          <>
            <Table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Playbook</th>
                  <th>Targets</th>
                  <th>Mode</th>
                  <th>Source</th>
                  <th>Took</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.map((job: JobSummary) => (
                  <tr key={job.job_id}>
                    <td><DateTime value={job.started_at} /></td>
                    <td><Mono>{job.playbook_name}</Mono></td>
                    <td>{job.limit || "all"}</td>
                    <td>{job.check_mode ? <Tag tone="ok">check</Tag> : <Tag tone="warn">apply</Tag>}</td>
                    <td>{job.source}{job.actor ? ` · ${job.actor}` : ""}</td>
                    <td>{took(job)}</td>
                    <td><Tag tone={statusTone(job.status)}>{job.status}</Tag></td>
                    <td><Button onClick={() => void open(job.job_id)}>Open</Button></td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination
              page={page}
              pageCount={Math.max(1, Math.ceil((state.data.total ?? jobs.length) / JOBS_PAGE_SIZE))}
              onPage={setPage}
              total={state.data.total ?? jobs.length}
              matched={state.data.total ?? jobs.length}
              noun="jobs"
            />
          </>
        ) : (
          <p>{status || playbook || source ? "No jobs match these filters." : "Nothing has run yet."}</p>
        )}
      </Card>

      {selected && <JobDetailCard job={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function JobDetailCard({ job, onClose }: { job: JobDetail; onClose: () => void }) {
  const commands = job.events.flatMap((event) => event.commands ?? []);
  return (
    <Card
      title={job.playbook_name}
      description={`${job.status}${job.exit_code === null ? "" : ` · exit ${job.exit_code}`} · ${when(job.started_at)}`}
      actions={<Button onClick={onClose}>Close</Button>}
    >
      {commands.length > 0 && (
        <>
          <h4>Commands</h4>
          <pre className={styles.pre}>{commands.join("\n")}</pre>
        </>
      )}
      {job.error && (
        <>
          <h4>Why it stopped</h4>
          <p>{job.error}</p>
        </>
      )}
      <h4>Output</h4>
      {/* Device passwords and community strings are stripped from this, and kept
          in the commands above. See executor/redaction.py: an approver has to be
          able to check a value they are about to push; a stdout tail is where a
          missing no_log ends up. */}
      <pre className={styles.pre}>{job.output || "No output was captured."}</pre>
    </Card>
  );
}

/* -------------------------------- changes -------------------------------- */

/** Shown when the signed-in engineer approves their own change (ADR 0039). */
export { SELF_APPROVAL_COPY };

function ActorCell({
  who,
  at,
}: {
  who: string;
  at?: number | null;
}) {
  return (
    <td>
      {who || "—"}
      {at ? (
        <>
          <br />
          <DateTime value={at} />
        </>
      ) : null}
    </td>
  );
}

/** Exported for vitest; the Network screen mounts this under the Changes tab. */
export function ChangesPanel() {
  const session = useSession();
  const { state, reload } = useAsync(() => getChangesAwaitingApproval(), []);
  const dialogs = useConfirm();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChangeDetail | null>(null);

  async function loadDetail(changeId: string, show = true, fresh = false): Promise<ChangeDetail> {
    if (!fresh && detail?.id === changeId) return detail;
    const result = await getChangeForReview(changeId);
    if (result.ok && result.change) {
      if (show || detail?.id === changeId) setDetail(result.change);
      return result.change;
    }
    throw new Error(result.error || "Could not load that change.");
  }

  async function review(change: ChangeSummary) {
    if (detail?.id === change.id) {
      setDetail(null);
      return;
    }
    try {
      await loadDetail(change.id, true);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function decide(change: ChangeSummary, decision: "approve" | "reject") {
    let plan: ChangeDetail | null = detail?.id === change.id ? detail : null;
    if (decision === "approve") {
      try {
        // Read again, not from the panel's copy: the dialog must show the plan
        // the approval is bound to, and that is the plan as it stands now.
        plan = await loadDetail(change.id, false, true);
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : String(caught));
        return;
      }
    }

    const isRequester = change.requested_by === session.user.id;
    let typed = "";
    if (decision === "approve") {
      if (!plan) return;
      const answer = await askToApprove(dialogs, {
        title: `Approve "${change.playbook_name}"?`,
        plan,
        intro: isRequester ? (
          <p>{SELF_APPROVAL_COPY}</p>
        ) : (
          <p>
            Approving binds to the commands as they stand now. If the devices
            move afterwards the change is refused as superseded rather than
            applied.
          </p>
        ),
      });
      if (answer === null) return;
      typed = answer;
    } else if (
      !(await dialogs.confirm({
        title: `Reject "${change.playbook_name}"?`,
        body: "The change will not be applied.",
        danger: true,
        confirmLabel: "Reject",
      }))
    ) {
      return;
    }
    setBusy(change.id);
    try {
      await decideChange(change.id, decision, plan?.current_hash, typed);
      toast.success(decision === "approve" ? "Change approved." : "Change rejected.");
      setDetail(null);
      reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }

  if (state.status === "loading") return <CenteredMessage title="Loading changes…" />;
  if (state.status === "error") {
    return (
      <LoadFailed what="changes" error={state.error} onRetry={reload} />
    );
  }

  const changes: ChangeSummary[] = state.data.changes ?? [];

  return (
    <Card
      title="Waiting for approval"
      description="Approving binds to the commands as they stand now"
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      {changes.length ? (
        <>
          <Table>
            <thead>
              <tr>
                <th>Raised</th>
                <th>Playbook</th>
                <th>Summary</th>
                <th>Targets</th>
                <th>Commands</th>
                <th>Requested by</th>
                <th>Approved by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {changes.map((change) => (
                <tr key={change.id}>
                  <td><DateTime value={change.created_at} /></td>
                  <td><Mono>{change.playbook_name}</Mono></td>
                  <td>{change.summary}</td>
                  <td>{change.limit}</td>
                  <td>{change.command_count}</td>
                  <ActorCell who={change.requested_by} at={change.created_at} />
                  <ActorCell
                    who={change.approved_by}
                    {...(change.approved_at != null ? { at: change.approved_at } : {})}
                  />
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <Button onClick={() => void review(change)}>
                      {detail?.id === change.id ? "Hide" : "Review"}
                    </Button>{" "}
                    <Button disabled={busy === change.id} onClick={() => void decide(change, "approve")}>
                      Approve
                    </Button>{" "}
                    <Button
                      variant="danger"
                      disabled={busy === change.id}
                      onClick={() => void decide(change, "reject")}
                    >
                      Reject
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
          {detail && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <p>
                Requested by {detail.requested_by || "—"}
                {" · "}
                <DateTime value={detail.created_at} />
                {" · "}
                Approved by {detail.approved_by || "—"}
                {detail.approved_at ? (
                  <>
                    {" · "}
                    <DateTime value={detail.approved_at} />
                  </>
                ) : null}
              </p>
              <h4>Commands for {detail.playbook_name}</h4>
              <pre className={styles.pre}>{detail.commands.join("\n") || "No commands."}</pre>
              {detail.rollback_commands.length > 0 && (
                <>
                  <h4>Rollback</h4>
                  <pre className={styles.pre}>{detail.rollback_commands.join("\n")}</pre>
                </>
              )}
              {detail.error && (
                <ScopeNote tone="crit">
                  <div>{detail.error}</div>
                </ScopeNote>
              )}
            </div>
          )}
        </>
      ) : (
        <p>Nothing is waiting for a decision.</p>
      )}
    </Card>
  );
}

/* --------------------------------- health -------------------------------- */

function HealthPanel() {
  const { state, reload } = useAsync(() => testNetworkExecutor(), []);

  if (state.status === "loading") return <CenteredMessage title="Asking the executor…" />;
  if (state.status === "error") {
    return <CenteredMessage title="Could not ask the executor" detail={state.error.message} />;
  }

  const probe: NetworkState = state.data;

  return (
    <>
      <Card
        title="Executor"
        description="What it answered just now"
        actions={<Button onClick={reload}>Check again</Button>}
      >
        <Table>
          <tbody>
            <tr>
              <td>Reachable</td>
              <td>
                {probe.reachable ? (
                  <Tag tone="ok">answered</Tag>
                ) : (
                  <Tag tone="crit">{probe.detail || "no answer"}</Tag>
                )}
              </td>
            </tr>
            <tr>
              <td>Catalogue</td>
              <td>
                <Mono>{probe.catalogue_commit || "unpinned"}</Mono>{" "}
                · {probe.catalogue_entries?.length ?? 0} entries
              </td>
            </tr>
            <tr>
              <td>Ansible</td>
              <td><Mono>{probe.ansible_version || "—"}</Mono></td>
            </tr>
            <tr>
              <td>Queue</td>
              <td>
                {probe.queue?.running ?? 0} running, {probe.queue?.queued ?? 0} waiting
                {probe.limits?.max_jobs_per_minute
                  ? ` · ${probe.limits.max_jobs_per_minute}/min, ${probe.limits.max_concurrent_jobs} at once`
                  : ""}
              </td>
            </tr>
          </tbody>
        </Table>
      </Card>

      {probe.collections && Object.keys(probe.collections).length > 0 && (
        <Card
          title="Collections"
          description="Pinned into the image at build time — a rebuild is the only thing that changes them"
        >
          <Table>
            <tbody>
              {Object.entries(probe.collections).map(([name, version]) => (
                <tr key={name}>
                  <td><Mono>{name}</Mono></td>
                  <td><Mono>{version}</Mono></td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Card title="Restarting it" description="Not something this screen can do">
        <ScopeNote tone="warn">
          <div>
            <strong>This application does not control the container.</strong> It
            manages what the executor holds — the inventory, the credentials — and
            reports what it says about itself. Starting and stopping it would mean
            handing this application a Docker socket, which is root on the host,
            and nothing here holds one.
          </div>
        </ScopeNote>
        <p>
          On Compose, <Mono>restart: unless-stopped</Mono> already brings it back
          from a crash. To restart it deliberately:
        </p>
        <pre className={styles.pre}>docker compose restart ansible</pre>
      </Card>
    </>
  );
}
