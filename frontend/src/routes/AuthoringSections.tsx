/**
 * Authoring surfaces for the Operations screen: projects, agent profiles,
 * skill import and instance lifecycle.
 */

import { useState } from "react";

import {
  importSkills,
  listEditableProfiles,
  listProjects,
  listScanTargets,
  previewSkillImport,
  withScratchContext,
  restartInstance,
  type AgentProfileDetail,
  type Project,
  type SkillImportPreview,
} from "@/api/authoring";
import { Button, Card, Mono, Row, ScopeNote, Table, Tag, useConfirm } from "@/components";
import { useAsync } from "@/lib/useAsync";

function Muted({ children }: { children: React.ReactNode }) {
  return (
    <p style={{ padding: "var(--space-3) 0", color: "var(--ink-3)", fontSize: "var(--text-sm)", margin: 0 }}>
      {children}
    </p>
  );
}

/* ---------- projects ---------- */

export function Projects() {
  const { state, reload } = useAsync<Project[]>(() => listProjects(), []);

  return (
    <Card
      title="Projects"
      description="A project scopes a conversation's files and knowledge to one folder."
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && <Muted>{state.error.message}</Muted>}
      {state.status === "ready" &&
        (state.data.length === 0 ? (
          <Muted>
            No projects. Conversations use the shared working directory until one
            is attached.
          </Muted>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Project</th>
                <th>Path</th>
              </tr>
            </thead>
            <tbody>
              {state.data.map((project) => (
                <tr key={project.name}>
                  <td>{project.title || project.name}</td>
                  <td>
                    <Mono>{project.path || "—"}</Mono>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        ))}
    </Card>
  );
}

/* ---------- agent profiles ---------- */

export function EditableProfiles() {
  const { state } = useAsync<AgentProfileDetail[]>(() => listEditableProfiles(), []);

  return (
    <Card
      title="Agent profile templates"
      description="Each profile is a set of prompt files. Editing one changes how every conversation using it behaves."
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && <Muted>{state.error.message}</Muted>}
      {state.status === "ready" && (
        <Table>
          <thead>
            <tr>
              <th>Profile</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {state.data.map((profile) => (
              <tr key={profile.id}>
                <td>
                  <strong>{profile.title || profile.id}</strong>
                  <div style={{ color: "var(--ink-3)" }}>
                    <Mono>{profile.id}</Mono>
                  </div>
                </td>
                <td style={{ color: "var(--ink-2)", maxWidth: "56ch" }}>
                  {profile.description || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

/* ---------- skills import ---------- */

export function SkillImport({
  onError,
  onNotice,
}: {
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SkillImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const { confirm } = useConfirm();
  const targets = useAsync(() => listScanTargets(), []);

  return (
    <Card
      title="Import skills"
      description="A skill is instructions the agent follows, so an archive is previewed before anything is written."
    >
      <Row label="Archive" help="Nothing is read from it until you choose Preview.">
        <input
          type="file"
          accept=".zip"
          aria-label="Skills archive"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setPreview(null);
          }}
        />
      </Row>

      {file && (
        <Row label="" help="Lists what the archive contains and what it would replace.">
          <Button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const result = await withScratchContext((ctx) => previewSkillImport(file, ctx));
                if (result.error) throw new Error(result.error);
                setPreview(result);
              } catch (caught) {
                onError(caught instanceof Error ? caught.message : String(caught));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Reading…" : preview ? "Re-read" : "Preview"}
          </Button>
        </Row>
      )}

      {preview && (
        <>
          <Row label="Contains">
            <Tag tone="accent">{preview.skills?.length ?? 0} skills</Tag>
          </Row>
          {preview.conflicts && preview.conflicts.length > 0 && (
            <>
              <Row label="Would replace">
                <Tag tone="warn">{preview.conflicts.length}</Tag>
              </Row>
              <ScopeNote tone="warn">
                <div>
                  <strong>{preview.conflicts.length} existing skills would be replaced:</strong>{" "}
                  {preview.conflicts.join(", ")}
                </div>
              </ScopeNote>
            </>
          )}
          <Row label="" help="Imported skills become available to every user's agent.">
            <Button
              variant="primary"
              disabled={busy || !file}
              onClick={async () => {
                if (!file) return;
                const ok = await confirm({
                  title: `Import ${preview.skills?.length ?? 0} skills?`,
                  body: "They become available to every user's agent.",
                  confirmLabel: "Import",
                });
                if (!ok) return;
                setBusy(true);
                try {
                  const result = await withScratchContext((ctx) => importSkills(file, ctx));
                  if (result.error) throw new Error(result.error);
                  onNotice(`Imported ${result.imported?.length ?? 0} skills.`);
                  setPreview(null);
                  setFile(null);
                } catch (caught) {
                  onError(caught instanceof Error ? caught.message : String(caught));
                } finally {
                  setBusy(false);
                }
              }}
            >
              Import
            </Button>
          </Row>
        </>
      )}

      {targets.state.status === "ready" && (
        <Row
          label="Reviewable targets"
          help="Skill sources a review pass can be run against."
        >
          <Tag tone="muted">{targets.state.data.targets?.length ?? 0}</Tag>
        </Row>
      )}
    </Card>
  );
}

/* ---------- lifecycle ---------- */

export function InstanceLifecycle() {
  const [busy, setBusy] = useState(false);
  const { confirm } = useConfirm();

  return (
    <Card
      title="Restart"
      description="Reloads the server process. Every signed-in session is interrupted and any running agent is stopped."
      danger
    >
      <Row
        label="Restart this instance"
        help="Use after changing configuration that is read at startup. Conversations are preserved; work in progress is not."
      >
        <Button
          variant="danger"
          disabled={busy}
          onClick={async () => {
            const ok = await confirm({
              title: "Restart the instance?",
              body: "Everyone's session is interrupted and running agents are stopped.",
              confirmLabel: "Restart",
              danger: true,
            });
            if (!ok) return;
            setBusy(true);
            await restartInstance();
            // The process is coming back up; a reload lands on it rather than on
            // a dead connection.
            window.setTimeout(() => window.location.reload(), 4000);
          }}
        >
          {busy ? "Restarting…" : "Restart"}
        </Button>
      </Row>
    </Card>
  );
}
