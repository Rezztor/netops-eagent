/**
 * Operations: scheduled tasks, MCP servers, skills, agent profiles, memory,
 * backup and instance lifecycle.
 *
 * Grouped as one screen with sections rather than six navigation entries. These
 * are things an administrator checks occasionally and rarely in isolation — a
 * separate destination each would make the rail longer without making anything
 * easier to find.
 */

import { useState } from "react";

import {
  reconnectMcpServers,
  deleteTask,
  getBackupDefaults,
  getMcpServerDetail,
  getMcpServerLog,
  listAgentProfiles,
  listMcpServers,
  listTasks,
  getMemoryDashboard,
  importKnowledge,
  reindexKnowledge,
  runTask,
  setTaskState,
  taskId,
  testBackupPatterns,
  type AgentProfile,
  type BackupDefaults,
  type McpServerStatus,
  type ScheduledTask,
} from "@/api/operations";
import { PERMISSIONS } from "@/api/types";
import {
  Button,
  Card,
  DateTime,
  Drawer,
  Mono,
  Row,
  ScopeNote,
  Table,
  Tag,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useRowBusy } from "@/lib/useRowBusy";
import { BackupSection } from "./BackupSection";
import { withScratchContext } from "@/api/authoring";
import { Link } from "react-router-dom";
import {
  EditableProfiles,
  Projects,
} from "./AuthoringSections";
import { useSession } from "@/lib/session";
import shell from "./shell.module.css";

const SECTIONS = [
  { id: "tasks", label: "Scheduled tasks" },
  { id: "mcp", label: "MCP servers" },
  { id: "profiles", label: "Agent profiles" },
  { id: "projects", label: "Projects" },
  { id: "memory", label: "Memory & knowledge" },
] as const;

export function OperationsScreen() {
  const session = useSession();
  const toast = useToast();
  // Every section already takes onError/onNotice; backing them with the toast
  // service turns the whole screen over to transient feedback without touching
  // each child.
  const onError = (message: string) => toast.error(message);
  const onNotice = (message: string) => toast.success(message);

  return (
    <div className={shell.stack}>
      <ScopeNote>
        <div>
          <strong>Administrator only.</strong> Everything here applies to the
          instance and every account on it, not to yours alone.
        </div>
      </ScopeNote>

      <ScopeNote>
        <div>
          Updates, backup, restart and the support bundle are together in{" "}
          <Link to="/settings/updates">Settings, Product</Link>.
        </div>
      </ScopeNote>

      <nav
        aria-label="Sections"
        style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", fontSize: "var(--text-sm)" }}
      >
        {SECTIONS.map((section) => (
          <a key={section.id} href={`#${section.id}`} style={{ color: "var(--accent)" }}>
            {section.label}
          </a>
        ))}
      </nav>

      <section id="tasks">
        <ScheduledTasks
          timezone={session.preferences.timezone}
          onError={onError}
          onNotice={onNotice}
        />
      </section>
      <section id="mcp">
        <McpServers onError={onError} onNotice={onNotice} />
      </section>
      <section id="profiles">
        <AgentProfiles />
        <EditableProfiles />
      </section>
      <section id="projects">
        <Projects />
      </section>
      <section id="memory">
        <MemoryAndKnowledge onError={onError} onNotice={onNotice} />
      </section>
    </div>
  );
}

/* ---------- scheduled tasks ---------- */

function ScheduledTasks({
  timezone,
  onError,
  onNotice,
}: {
  timezone: string;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const { state, reload } = useAsync<ScheduledTask[]>(() => listTasks(timezone), [timezone]);
  const rowBusy = useRowBusy();
  const { confirm } = useConfirm();

  async function act(id: string, action: () => Promise<{ error?: string }>, success: string) {
    await rowBusy.run(id, async () => {
      try {
        const result = await action();
        if (result.error) throw new Error(result.error);
        onNotice(success);
        reload();
      } catch (caught) {
        onError(caught instanceof Error ? caught.message : String(caught));
      }
    });
  }

  return (
    <Card
      title="Scheduled tasks"
      description="Everyone's agents that run unattended."
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && <Muted>{state.error.message}</Muted>}
      {state.status === "ready" &&
        (state.data.length === 0 ? (
          <Muted>
            No scheduled tasks. A task runs an agent on a schedule with nobody
            watching, so they are worth reviewing when they exist.
          </Muted>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Type</th>
                <th>State</th>
                <th>Last run</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {state.data.map((task) => {
                const id = taskId(task);
                const busy = rowBusy.isBusy(id);
                const running = task.state === "running";
                const paused = task.state === "disabled";
                return (
                  <tr key={id}>
                    <td>
                      <strong>{task.name || "Untitled"}</strong>
                      <div style={{ color: "var(--ink-3)" }}>
                        <Mono>{id.slice(0, 12)}</Mono>
                      </div>
                    </td>
                    <td>
                      <Tag tone="muted">{task.type || "—"}</Tag>
                    </td>
                    <td>
                      <Tag tone={running ? "warn" : task.state === "error" ? "crit" : paused ? "muted" : "ok"}>
                        {paused ? "paused" : task.state || "idle"}
                      </Tag>
                    </td>
                    <td style={{ color: "var(--ink-3)" }}>
                      {task.last_run ? <DateTime value={String(task.last_run)} /> : "never"}
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <Button
                        disabled={busy || running}
                        onClick={() => void act(id, () => runTask(id, timezone), `Task "${task.name}" started.`)}
                      >
                        Run now
                      </Button>{" "}
                      <Button
                        disabled={busy || running}
                        title={running ? "Wait for the current run to finish." : undefined}
                        onClick={() =>
                          void act(
                            id,
                            () => setTaskState(id, paused ? "idle" : "disabled", timezone),
                            paused ? `Task "${task.name}" resumed.` : `Task "${task.name}" paused.`,
                          )
                        }
                      >
                        {paused ? "Resume" : "Pause"}
                      </Button>{" "}
                      <Button
                        variant="danger"
                        disabled={busy}
                        onClick={async () => {
                          const ok = await confirm({
                            title: `Delete "${task.name}"?`,
                            body: "It will stop running and be removed from the scheduler. This cannot be undone.",
                            danger: true,
                            confirmLabel: "Delete task",
                          });
                          if (ok) void act(id, () => deleteTask(id, timezone), `Task "${task.name}" deleted.`);
                        }}
                      >
                        Delete
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        ))}
    </Card>
  );
}

/* ---------- MCP servers ---------- */

function McpServers({
  onError,
  onNotice,
}: {
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const { state, reload } = useAsync<McpServerStatus[]>(() => listMcpServers(), []);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);

  return (
    <Card
      title="MCP servers"
      description="External tool servers this agent can call. Configured in Settings; their connection state is shown here."
      actions={
        <Button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await reconnectMcpServers();
              onNotice("MCP server configuration reapplied.");
              reload();
            } catch (caught) {
              onError(caught instanceof Error ? caught.message : String(caught));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Applying…" : "Reconnect"}
        </Button>
      }
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && <Muted>{state.error.message}</Muted>}
      {state.status === "ready" &&
        (state.data.length === 0 ? (
          <Muted>No MCP servers configured.</Muted>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Server</th>
                <th>State</th>
                <th style={{ textAlign: "right" }}>Tools</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {state.data.map((server, index) => (
                <tr key={server.name || index}>
                  <td>
                    <strong>{server.name || "Unnamed"}</strong>
                    {server.error && (
                      <div style={{ color: "var(--crit)", fontSize: "var(--text-xs)" }}>
                        {server.error}
                      </div>
                    )}
                  </td>
                  <td>
                    <Tag tone={server.connected ? "ok" : "crit"}>
                      {server.connected ? "connected" : "disconnected"}
                    </Tag>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {server.tool_count ?? (Array.isArray(server.tools) ? server.tools.length : "—")}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {server.name && (
                      <Button onClick={() => setDetail(server.name ?? null)}>Logs &amp; detail</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        ))}
      {detail && <McpDetailDrawer server={detail} onClose={() => setDetail(null)} />}
    </Card>
  );
}

/**
 * A server's configuration detail and its connection log, fetched on open.
 *
 * `mcp_server_get_detail` and `mcp_server_get_log` were reachable but unused:
 * the status table said connected/disconnected without ever showing *why* a
 * disconnected server failed. This puts the initialisation log -- where the
 * actual error is -- one click from the row that reports the failure.
 */
function McpDetailDrawer({ server, onClose }: { server: string; onClose: () => void }) {
  const log = useAsync(() => getMcpServerLog(server), [server]);
  const detail = useAsync(() => getMcpServerDetail(server), [server]);

  return (
    <Drawer title={`MCP · ${server}`} onClose={onClose}>
      <h3 style={{ fontSize: "var(--text-sm)", color: "var(--ink-2)", margin: "0 0 var(--space-1)" }}>
        Connection log
      </h3>
      {log.state.status === "loading" && <Muted>Loading log…</Muted>}
      {log.state.status === "error" && <Muted>{log.state.error.message}</Muted>}
      {log.state.status === "ready" && (
        <pre
          style={{
            whiteSpace: "pre-wrap",
            fontSize: "var(--text-xs)",
            background: "var(--surface-alt)",
            padding: "var(--space-2)",
            borderRadius: "var(--radius-sm)",
            maxHeight: 260,
            overflow: "auto",
            margin: 0,
          }}
        >
          {log.state.data.log?.trim() || "No log output."}
        </pre>
      )}

      <h3 style={{ fontSize: "var(--text-sm)", color: "var(--ink-2)", margin: "var(--space-3) 0 var(--space-1)" }}>
        Detail
      </h3>
      {detail.state.status === "loading" && <Muted>Loading detail…</Muted>}
      {detail.state.status === "error" && <Muted>{detail.state.error.message}</Muted>}
      {detail.state.status === "ready" && (
        <pre
          style={{
            whiteSpace: "pre-wrap",
            fontSize: "var(--text-xs)",
            background: "var(--surface-alt)",
            padding: "var(--space-2)",
            borderRadius: "var(--radius-sm)",
            maxHeight: 260,
            overflow: "auto",
            margin: 0,
          }}
        >
          {JSON.stringify(detail.state.data.detail ?? {}, null, 2)}
        </pre>
      )}
    </Drawer>
  );
}

/* ---------- skills ---------- */

/* ---------- agent profiles ---------- */

function AgentProfiles() {
  const { state } = useAsync<AgentProfile[]>(() => listAgentProfiles(), []);

  return (
    <Card
      title="Agent profiles"
      description="Each profile is a different prompt and tool set. New conversations start from the default."
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && <Muted>{state.error.message}</Muted>}
      {state.status === "ready" &&
        (state.data.length === 0 ? (
          <Muted>No agent profiles found.</Muted>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Profile</th>
                <th>Key</th>
              </tr>
            </thead>
            <tbody>
              {state.data.map((profile) => (
                <tr key={profile.key}>
                  <td>{profile.label}</td>
                  <td>
                    <Mono>{profile.key}</Mono>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        ))}
    </Card>
  );
}

/* ---------- memory and knowledge ---------- */

function MemoryAndKnowledge({
  onError,
  onNotice,
}: {
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const session = useSession();
  const canManage = session.permissions.includes(PERMISSIONS.pluginsManage);
  const { state, reload } = useAsync<Record<string, unknown>>(() => getMemoryDashboard(), []);
  const [busy, setBusy] = useState<"import" | "reindex" | null>(null);

  // Both operations take a ctxid, because the knowledge subdirectory comes from
  // the context's agent profile. Neither is really conversation-scoped from a
  // user's point of view -- the destination is the shared knowledge base either
  // way -- so a scratch context supplies the default scope and is removed after.
  async function run(kind: "import" | "reindex", action: (ctx: string) => Promise<unknown>) {
    setBusy(kind);
    try {
      await withScratchContext(action);
      onNotice(
        kind === "import"
          ? "Added to the knowledge base. Reindex to make it searchable."
          : "Knowledge index rebuilt.",
      );
      reload();
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }

  async function onFiles(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    // Reset first: picking the same file twice in a row fires no change event
    // otherwise, and the second attempt looks like the button is dead.
    event.target.value = "";
    if (files.length === 0) return;
    await run("import", (ctx) => importKnowledge(ctx, files));
  }

  return (
    <Card
      title="Memory and knowledge"
      description="Memory is per user. The knowledge base is shared: everything added here is readable by every agent on this instance."
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && <Muted>{state.error.message}</Muted>}
      {state.status === "ready" && (
        <>
          {Object.entries(state.data)
            .filter(([, value]) => typeof value === "number" || typeof value === "string")
            .slice(0, 8)
            .map(([key, value]) => (
              <Row key={key} label={key.replace(/_/g, " ")}>
                <Mono>{String(value)}</Mono>
              </Row>
            ))}

          {canManage ? (
            <>
              <Row
                label="Add knowledge"
                help="Documents the agent can quote from. They are shared with everyone on this instance, so treat this as publishing rather than uploading."
              >
                <input
                  type="file"
                  multiple
                  aria-label="Knowledge documents"
                  disabled={busy !== null}
                  onChange={(event) => void onFiles(event)}
                />
              </Row>
              <Row
                label="Rebuild index"
                help="Newly added documents are not searchable until the index is rebuilt. This reads every document, so it takes a while on a large base."
              >
                <Button
                  onClick={() => void run("reindex", (ctx) => reindexKnowledge(ctx))}
                  disabled={busy !== null}
                >
                  {busy === "reindex" ? "Rebuilding…" : "Reindex"}
                </Button>
              </Row>
            </>
          ) : (
            <Row
              label="Changing the knowledge base"
              help="Adding documents or rebuilding the index is for administrators, because everything here is read by every agent on the instance."
            >
              <Tag tone="muted">administrators only</Tag>
            </Row>
          )}
        </>
      )}
    </Card>
  );
}

/* ---------- backup ---------- */

export function Backup({
  onError,
  onNotice,
}: {
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const { state } = useAsync<BackupDefaults>(() => getBackupDefaults(), []);
  const [preview, setPreview] = useState<{ total: number; files: string[] } | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);

  if (state.status === "loading") {
    return <Card title="Backup"><Muted>Loading…</Muted></Card>;
  }
  if (state.status === "error") {
    return <Card title="Backup" danger><Muted>{state.error.message}</Muted></Card>;
  }

  return (
    <>
      <Card title="What a backup contains" description="Verify before relying on one.">
        <Row label="Excluded">
          <Tag tone="muted">{state.data.default_patterns.exclude_patterns.length} patterns</Tag>
        </Row>
        <Row
          label="Dry run"
          help={
            preview
              ? `${preview.total} files would be included.`
              : "Count the files without writing an archive."
          }
        >
          <Button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const result = await testBackupPatterns(
                  state.data.default_patterns.include_patterns,
                  state.data.default_patterns.exclude_patterns,
                  Boolean(state.data.metadata.include_hidden),
                );
                if (!result.success) throw new Error(result.error || "Dry run failed.");
                setPreview({
                  total: result.total_files ?? (result.files?.length ?? 0),
                  files: (result.files ?? []).map((entry) => entry.path).filter(Boolean),
                });
                setExpanded(false);
              } catch (caught) {
                onError(caught instanceof Error ? caught.message : String(caught));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Counting…" : "Count files"}
          </Button>
        </Row>
        {preview && preview.files.length > 0 && (
          <Row
            label="Matched paths"
            help={
              preview.total > preview.files.length
                ? `Showing the first ${preview.files.length} of ${preview.total}.`
                : "The exact files an archive would contain."
            }
          >
            <div style={{ textAlign: "right", width: "100%" }}>
              <Button onClick={() => setExpanded((current) => !current)}>
                {expanded ? "Hide paths" : `Show ${preview.files.length} paths`}
              </Button>
              {expanded && (
                <div
                  style={{
                    marginTop: "var(--space-2)",
                    maxHeight: 240,
                    overflow: "auto",
                    textAlign: "left",
                    background: "var(--surface-alt)",
                    borderRadius: "var(--radius-sm)",
                    padding: "var(--space-2)",
                  }}
                >
                  {preview.files.map((path) => (
                    <div key={path} style={{ fontSize: "var(--text-xs)" }}>
                      <Mono>{path}</Mono>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Row>
        )}
      </Card>

      <BackupSection defaults={state.data} onError={onError} onNotice={onNotice} />
    </>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return (
    <p style={{ padding: "var(--space-3) 0", color: "var(--ink-3)", fontSize: "var(--text-sm)", margin: 0 }}>
      {children}
    </p>
  );
}
