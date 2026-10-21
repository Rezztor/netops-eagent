/**
 * The operational admin surfaces: scheduled tasks, MCP servers, skills, agent
 * profiles, memory, and backup.
 *
 * These share a shape but not a convention. Some return `ok`, others `success`;
 * `agents` returns its list under `data` while `skills` uses `skills`; several
 * take an `action` discriminator rather than having their own endpoint. Each
 * wrapper here normalises one of those so the screens do not have to remember
 * which is which.
 */

import { apiDownload, apiRequest, apiUpload } from "./client";

/* ---------- scheduled tasks ---------- */

export interface ScheduledTask {
  uuid?: string;
  id?: string;
  name?: string;
  type?: string;
  state?: string;
  system_prompt?: string;
  prompt?: string;
  last_run?: string | null;
  last_result?: string | null;
  created_at?: string;
  schedule?: Record<string, unknown>;
  [key: string]: unknown;
}

export async function listTasks(timezone: string): Promise<ScheduledTask[]> {
  const result = await apiRequest<{ ok: boolean; tasks?: ScheduledTask[]; error?: string }>(
    "scheduler_tasks_list",
    { body: { timezone } },
  );
  if (!result.ok) throw new Error(result.error || "Could not list scheduled tasks.");
  return result.tasks ?? [];
}

/** Identifiers differ by task type, so accept whichever the list carried. */
export function taskId(task: ScheduledTask): string {
  return String(task.uuid || task.id || "");
}

export function runTask(id: string, timezone: string) {
  return apiRequest<{ success?: boolean; error?: string; message?: string }>(
    "scheduler_task_run",
    { body: { task_id: id, timezone } },
  );
}

export function deleteTask(id: string, timezone: string) {
  return apiRequest<{ success?: boolean; error?: string }>("scheduler_task_delete", {
    body: { task_id: id, timezone },
  });
}

export function setTaskState(id: string, state: string, timezone: string) {
  return apiRequest<{ success?: boolean; error?: string }>("scheduler_task_update", {
    body: { task_id: id, state, timezone },
  });
}

/* ---------- MCP servers ---------- */

export interface McpServerStatus {
  name?: string;
  connected?: boolean;
  error?: string;
  tool_count?: number;
  tools?: unknown[];
  [key: string]: unknown;
}

export async function listMcpServers(): Promise<McpServerStatus[]> {
  const result = await apiRequest<{ success: boolean; status?: McpServerStatus[]; error?: string }>(
    "mcp_servers_status",
  );
  if (!result.success) throw new Error(result.error || "Could not read MCP server status.");
  return result.status ?? [];
}

/**
 * Reapply the MCP configuration, forcing servers to reconnect.
 *
 * The endpoint applies a configuration rather than refreshing the current one --
 * it requires `mcp_servers` and raises KeyError without it. To mean "reconnect",
 * read the saved configuration and send it back unchanged, which is what the
 * handler's clear-then-set does internally to force reinitialisation.
 */
export async function reconnectMcpServers(projectName = ""): Promise<McpServerStatus[]> {
  const settings = await apiRequest<{ settings: { mcp_servers?: string } }>("settings_get");
  const config = settings.settings.mcp_servers ?? "{}";
  const result = await apiRequest<{
    success: boolean;
    error?: string;
    status?: McpServerStatus[];
  }>("mcp_servers_apply", { body: { mcp_servers: config, project_name: projectName } });
  if (!result.success) throw new Error(result.error || "Could not reapply MCP configuration.");
  return result.status ?? [];
}

export function getMcpServerDetail(serverName: string, projectName = "") {
  return apiRequest<{ success: boolean; error?: string; detail?: unknown }>(
    "mcp_server_get_detail",
    { body: { server_name: serverName, project_name: projectName } },
  );
}

export function getMcpServerLog(serverName: string, projectName = "") {
  return apiRequest<{ success: boolean; error?: string; log?: string }>("mcp_server_get_log", {
    body: { server_name: serverName, project_name: projectName },
  });
}

/* ---------- skills ---------- */

export interface Skill {
  name: string;
  description?: string;
  origin?: string;
  path?: string;
  triggers?: string;
  [key: string]: unknown;
}

export interface SkillCatalog {
  skills: Skill[];
  active_skills: string[];
  disabled_skills: string[];
  max_active_skills?: number;
}

/**
 * The catalog, not `skills` with action=list.
 *
 * `api/skills.py` lists user- and project-authored skills only, which is empty
 * on a stock instance; the plugin's catalog carries the bundled ones too, which
 * is what an administrator expects to see.
 */
export async function getSkillCatalog(): Promise<SkillCatalog> {
  const result = await apiRequest<{
    ok: boolean;
    skills?: Skill[];
    active_skills?: string[];
    disabled_skills?: string[];
    max_active_skills?: number;
    error?: string;
  }>("plugins/_skills/skills_catalog", { body: {} });
  if (!result.ok) throw new Error(result.error || "Could not load the skill catalog.");
  return {
    skills: result.skills ?? [],
    active_skills: result.active_skills ?? [],
    disabled_skills: result.disabled_skills ?? [],
    ...(result.max_active_skills !== undefined
      ? { max_active_skills: result.max_active_skills }
      : {}),
  };
}

/* ---------- agent profiles ---------- */

export interface AgentProfile {
  key: string;
  label: string;
  [key: string]: unknown;
}

/** Returns its list under `data`, unlike its neighbours. */
export async function listAgentProfiles(): Promise<AgentProfile[]> {
  const result = await apiRequest<{ ok: boolean; data?: AgentProfile[]; error?: string }>(
    "agents",
    { body: { action: "list" } },
  );
  if (!result.ok) throw new Error(result.error || "Could not list agent profiles.");
  return result.data ?? [];
}

/**
 * Switch the profile a conversation runs under.
 *
 * `context_id` is required rather than optional: `api/agent_profile_set.py`
 * answers 400 without one, so a call that omits it changes nothing while
 * looking like a request. The endpoint also refuses with 409 while that
 * conversation is mid-run -- the profile is swapped between runs, not during
 * one -- and that reason arrives as the error text, so callers should show it
 * rather than retry.
 */
export function setAgentProfile(contextId: string, profile: string) {
  return apiRequest<{ ok?: boolean; error?: string; agent_profile_label?: string }>(
    "agent_profile_set",
    { body: { context_id: contextId, agent_profile: profile } },
  );
}

/* ---------- memory and knowledge ---------- */

/**
 * Both of these are scoped to a conversation, not to the instance.
 *
 * They take a `ctxid` and raise without one, because the knowledge base an
 * agent reads depends on its context and project. That makes them chat-side
 * calls rather than administration, despite reading like instance settings --
 * calling them from an admin screen with no conversation in hand returns a 500.
 */
export function getKnowledgePath(contextId: string) {
  return apiRequest<{ ok?: boolean; path?: string; error?: string }>(
    "plugins/_memory/knowledge_path_get",
    { body: { ctxid: contextId } },
  );
}

export function reindexKnowledge(contextId: string) {
  return apiRequest<{ ok?: boolean; error?: string; message?: string }>(
    "plugins/_memory/knowledge_reindex",
    { body: { ctxid: contextId } },
  );
}

/**
 * Add files to the shared knowledge base.
 *
 * Shared, not per-user: it writes into `usr/knowledge`, which every agent reads
 * and quotes back. That is why the endpoint requires `plugins.manage` -- an
 * ungated import would be a way to put text in front of everyone else's agent.
 *
 * Takes a `ctxid` like its neighbours, because the destination subdirectory
 * comes from the context's agent profile. Callers pass a scratch context to get
 * the default one.
 */
export function importKnowledge(contextId: string, files: File[]) {
  const form = new FormData();
  form.append("ctxid", contextId);
  // "files[]", not "files": the handler reads request.files.getlist("files[]")
  // and raises "No files part" for anything else.
  for (const file of files) form.append("files[]", file);
  return apiUpload<{ message?: string; filenames?: string[] }>(
    "plugins/_memory/import_knowledge",
    form,
  );
}

/** Instance-wide, unlike its neighbours above. */
export function getMemoryDashboard() {
  return apiRequest<Record<string, unknown>>("plugins/_memory/memory_dashboard", { body: {} });
}

/* ---------- backup ---------- */

export interface BackupDefaults {
  default_patterns: { include_patterns: string[]; exclude_patterns: string[] };
  metadata: { backup_name?: string; include_hidden?: boolean; [key: string]: unknown };
}

export async function getBackupDefaults(): Promise<BackupDefaults> {
  const result = await apiRequest<{
    success: boolean;
    default_patterns?: BackupDefaults["default_patterns"];
    metadata?: BackupDefaults["metadata"];
    error?: string;
  }>("backup_get_defaults");
  if (!result.success) throw new Error(result.error || "Could not read backup defaults.");
  return {
    default_patterns: result.default_patterns ?? { include_patterns: [], exclude_patterns: [] },
    metadata: result.metadata ?? {},
  };
}

/** Dry run: which files a backup would include, without writing one. */
export function testBackupPatterns(
  includePatterns: string[],
  excludePatterns: string[],
  includeHidden = true,
) {
  return apiRequest<{
    success: boolean;
    files?: Array<{ path: string; size?: number }>;
    total_files?: number;
    error?: string;
  }>("backup_test", {
    body: {
      include_patterns: includePatterns,
      exclude_patterns: excludePatterns,
      include_hidden: includeHidden,
      max_files: 1000,
    },
  });
}

/* ---------- backup: create ---------- */

/**
 * Create and download a backup.
 *
 * Streams the archive back directly rather than returning a path, so this goes
 * through `apiDownload`.
 */
export function createBackup(
  name: string,
  includePatterns: string[],
  excludePatterns: string[],
  includeHidden = true,
): Promise<void> {
  return apiDownload("backup_create", {
    method: "POST",
    body: {
      backup_name: name,
      include_patterns: includePatterns,
      exclude_patterns: excludePatterns,
      include_hidden: includeHidden,
    },
    filename: `${name}.zip`,
  });
}

/* ---------- backup: restore ---------- */

export interface BackupInspection {
  success: boolean;
  error?: string;
  metadata?: Record<string, unknown>;
  backup_name?: string;
  timestamp?: string;
  eagent_version?: string;
  total_files?: number;
  backup_size?: number;
  include_patterns?: string[];
  exclude_patterns?: string[];
}

/** Read an archive's manifest without changing anything. */
export function inspectBackup(file: File) {
  const form = new FormData();
  form.append("backup_file", file);
  return apiUpload<BackupInspection>("backup_inspect", form);
}

/**
 * How an existing file is treated when the archive also contains it.
 *
 * `overwrite` replaces it, `skip` keeps what is on disk, `backup` moves the
 * current one aside first.
 */
export type OverwritePolicy = "overwrite" | "skip" | "backup";

export interface RestorePreview {
  success: boolean;
  error?: string;
  files_to_delete?: Array<{ path?: string; [key: string]: unknown }>;
  files_to_restore?: Array<{ path?: string; [key: string]: unknown }>;
  delete_count?: number;
  restore_count?: number;
  skipped_count?: number;
  total_count?: number;
}

/**
 * What a restore would do, without doing it.
 *
 * The important number is `delete_count`. With `cleanBeforeRestore` the restore
 * removes files that are *not* in the archive, which is how a restore turns into
 * data loss for anything created since the backup was taken. Nothing should call
 * `restoreBackup` without having shown this first.
 */
export function previewRestore(
  file: File,
  metadata: Record<string, unknown>,
  policy: OverwritePolicy,
  cleanBeforeRestore: boolean,
) {
  const form = new FormData();
  form.append("backup_file", file);
  form.append("metadata", JSON.stringify(metadata));
  form.append("overwrite_policy", policy);
  form.append("clean_before_restore", String(cleanBeforeRestore));
  return apiUpload<RestorePreview>("backup_restore_preview", form);
}

export interface RestoreResult {
  success: boolean;
  error?: string;
  restored_files?: unknown[];
  deleted_files?: unknown[];
  skipped_files?: unknown[];
  errors?: unknown[];
}

export function restoreBackup(
  file: File,
  metadata: Record<string, unknown>,
  policy: OverwritePolicy,
  cleanBeforeRestore: boolean,
) {
  const form = new FormData();
  form.append("backup_file", file);
  form.append("metadata", JSON.stringify(metadata));
  form.append("overwrite_policy", policy);
  form.append("clean_before_restore", String(cleanBeforeRestore));
  return apiUpload<RestoreResult>("backup_restore", form);
}
