/**
 * Skills: what the agent can be told to do, and how to change it.
 *
 * A destination of its own rather than a section under Operations, because a
 * skill is something a person works *with* -- they read what is available and
 * add to it -- while the rest of Operations is configuration an administrator
 * checks occasionally.
 *
 * Listing comes from the plugin catalog, which reads and is open to any signed-in
 * user. Writing -- import and delete -- requires `plugins.manage`, because a
 * skill is an instruction the agent loads on demand and changing the set changes
 * what every agent on the instance does. The server enforces that; the controls
 * are hidden without the permission only so the screen does not offer an action
 * that would fail.
 */

import type React from "react";
import { useState } from "react";

import { deleteSkill } from "@/api/authoring";
import { forgetSkill } from "@/api/autoSkills";
import { getSkillCatalog, type Skill, type SkillCatalog } from "@/api/operations";
import { PERMISSIONS } from "@/api/types";
import {
  Button,
  Card,
  Drawer,
  EmptyRow,
  Mono,
  Row,
  SearchInput,
  Select,
  Table,
  Tag,
  useConfirm,
  useToast,
} from "@/components";
import { useCan, useSession } from "@/lib/session";
import { useAsync } from "@/lib/useAsync";
import { useTableControls } from "@/lib/useTableControls";
import { AutoSkillsSettings, SkillPromotions } from "./AutoSkillsSections";
import { SkillImport } from "./AuthoringSections";

export function SkillsScreen() {
  const toast = useToast();
  // Both Auto Skills surfaces decide what every account's agent reads, and
  // their endpoints require plugins.manage. Hidden without it so the screen
  // does not offer an action that would 403 on arrival.
  const canManage = useCan(PERMISSIONS.pluginsManage);

  return (
    <>
      <SkillCatalogCard onError={toast.error} onNotice={toast.success} />
      {canManage && <SkillPromotions onError={toast.error} onNotice={toast.success} />}
      <SkillImport onError={toast.error} onNotice={toast.success} />
      {canManage && <AutoSkillsSettings onError={toast.error} onNotice={toast.success} />}
    </>
  );
}

const skillSearch = (skill: Skill) => [skill.name, skill.description, skill.origin];

function SkillCatalogCard({
  onError,
  onNotice,
}: {
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const session = useSession();
  const canManage = session.permissions.includes(PERMISSIONS.pluginsManage);
  const { state, reload } = useAsync<SkillCatalog>(() => getSkillCatalog(), []);
  const { confirm } = useConfirm();
  const [removing, setRemoving] = useState<string | null>(null);
  const [detail, setDetail] = useState<Skill | null>(null);
  const [origin, setOrigin] = useState("all");

  const skills = state.status === "ready" ? state.data.skills : [];
  const controls = useTableControls(skills, {
    search: skillSearch,
    filter: (skill) => origin === "all" || (skill.origin || "bundled") === origin,
    comparators: { name: (a, b) => a.name.localeCompare(b.name) },
    initialSort: { key: "name", direction: "asc" },
  });

  // A skill the agent induced from this user's own work. Nobody else's agent
  // reads it, it was applied without being asked for, and removing it goes
  // through a different endpoint that does not require plugins.manage.
  const isPersonal = (skill: Skill) => skill.origin === "Personal";

  async function forget(skill: Skill) {
    const ok = await confirm({
      title: `Remove "${skill.name}"?`,
      body: "Your agent will stop loading it.",
      danger: true,
      confirmLabel: "Forget",
    });
    if (!ok) return;
    setRemoving(skill.name);
    try {
      onNotice(await forgetSkill(skill.name));
      reload();
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemoving(null);
    }
  }

  async function remove(skill: Skill) {
    if (!skill.path) {
      // Bundled skills have no writable path. Without this the delete would
      // fail server-side with a less obvious message.
      onError(`${skill.name} ships with the product and cannot be removed.`);
      return;
    }
    const ok = await confirm({
      title: `Delete the skill "${skill.name}"?`,
      body: "This cannot be undone.",
      danger: true,
      confirmLabel: "Delete",
    });
    if (!ok) return;
    setRemoving(skill.name);
    try {
      await deleteSkill(skill.path);
      onNotice(`Deleted ${skill.name}.`);
      reload();
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemoving(null);
    }
  }

  return (
    <Card
      title="Skills"
      description="Instructions the agent can load on demand. Bundled skills ship with the product and cannot be removed."
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && (
        <div style={{ padding: "var(--space-3) 0" }}>
          <Muted>{state.error.message}</Muted>
          <div style={{ marginTop: "var(--space-2)" }}>
            <Button onClick={reload}>Retry</Button>
          </div>
        </div>
      )}
      {state.status === "ready" && (() => {
        const showActions = canManage || state.data.skills.some(isPersonal);
        const origins = Array.from(new Set(state.data.skills.map((s) => s.origin || "bundled")));
        const activeCount = state.data.active_skills.length;
        return (
        <>
          <Row
            label="Active vs available"
            help={
              state.data.max_active_skills
                ? `At most ${state.data.max_active_skills} can be active at once.`
                : "How many are loaded now, out of everything on offer."
            }
          >
            <span>
              <Tag tone={activeCount ? "accent" : "muted"}>{activeCount} active</Tag>{" "}
              <Tag tone="muted">{state.data.skills.length} available</Tag>
            </span>
          </Row>
          {state.data.disabled_skills.length > 0 && (
            <Row label="Disabled">
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 4,
                  justifyContent: "flex-end",
                  maxWidth: 420,
                }}
              >
                {state.data.disabled_skills.map((name) => (
                  <Tag key={name} tone="muted">
                    {name}
                  </Tag>
                ))}
              </div>
            </Row>
          )}
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
              value={controls.query}
              onChange={controls.setQuery}
              placeholder="Search skills…"
              label="Search skills"
            />
            <Select value={origin} onChange={(e) => setOrigin(e.target.value)} aria-label="Filter by origin">
              <option value="all">All origins</option>
              {origins.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </div>
          <div className="scroll-x">
            <Table>
              <thead>
                <tr>
                  <th>Skill</th>
                  <th>What it is for</th>
                  <th>Origin</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {controls.rows.length === 0 ? (
                  <EmptyRow colSpan={4}>No skills match your search.</EmptyRow>
                ) : (
                  controls.rows.map((skill) => (
                    <tr key={skill.name}>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <button
                          type="button"
                          onClick={() => setDetail(skill)}
                          style={{
                            border: 0,
                            background: "transparent",
                            color: "var(--accent)",
                            cursor: "pointer",
                            fontFamily: "var(--font-mono)",
                            padding: 0,
                          }}
                        >
                          {skill.name}
                        </button>
                        {state.data.active_skills.includes(skill.name) && (
                          <> <Tag tone="accent">active</Tag></>
                        )}
                      </td>
                      <td style={{ color: "var(--ink-2)", maxWidth: "52ch" }}>
                        {skill.description || "—"}
                      </td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <Tag tone="muted">{skill.origin || "bundled"}</Tag>
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        {showActions && (
                          <RowAction
                            skill={skill}
                            canManage={canManage}
                            busy={removing === skill.name}
                            onForget={() => void forget(skill)}
                            onDelete={() => void remove(skill)}
                          />
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </Table>
          </div>
          {detail && (
            <Drawer title={detail.name} onClose={() => setDetail(null)}>
              <Row label="Origin">
                <Tag tone="muted">{detail.origin || "bundled"}</Tag>
              </Row>
              {state.data.active_skills.includes(detail.name) && (
                <Row label="Status">
                  <Tag tone="accent">active</Tag>
                </Row>
              )}
              <Row label="What it is for">
                <span style={{ color: "var(--ink-2)" }}>{detail.description || "—"}</span>
              </Row>
              {detail.triggers && (
                <Row label="Triggers">
                  <Mono>{detail.triggers}</Mono>
                </Row>
              )}
              {detail.path && (
                <Row label="Location">
                  <Mono>{detail.path}</Mono>
                </Row>
              )}
            </Drawer>
          )}
        </>
        );
      })()}
    </Card>
  );
}

/**
 * What a row offers, which depends on whose skill it is.
 *
 * `Forget` and `Delete` are different actions against different endpoints, not
 * one action with two labels: forgetting reaches only the caller's own induced
 * skills and needs no permission, while deleting can reach the shared library
 * and requires `plugins.manage`.
 */
function RowAction({
  skill,
  canManage,
  busy,
  onForget,
  onDelete,
}: {
  skill: Skill;
  canManage: boolean;
  busy: boolean;
  onForget: () => void;
  onDelete: () => void;
}) {
  if (skill.origin === "Personal") {
    return (
      <Button variant="danger" onClick={onForget} disabled={busy}>
        {busy ? "Removing…" : "Forget"}
      </Button>
    );
  }
  if (!canManage) return null;
  if (!skill.path) return <Muted>bundled</Muted>;
  return (
    <Button variant="danger" onClick={onDelete} disabled={busy}>
      {busy ? "Deleting…" : "Delete"}
    </Button>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span style={{ color: "var(--ink-3)" }}>{children}</span>;
}
