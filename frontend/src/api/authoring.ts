/**
 * The remaining surfaces: projects, agent profiles, skills import, per-chat
 * model override, the document editor, and instance lifecycle.
 *
 * Grouped by what they let someone *author* rather than by which plugin owns
 * them, because that is how they are reached.
 */

import { apiDownload, apiObjectUrl, apiRequest, apiUpload } from "./client";

/* ---------- projects ---------- */

export interface Project {
  name: string;
  title?: string;
  path?: string;
  [key: string]: unknown;
}

export async function listProjects(): Promise<Project[]> {
  const result = await apiRequest<{ ok?: boolean; data?: Project[]; projects?: Project[]; error?: string }>(
    "projects",
    { body: { action: "list" } },
  );
  if (result.error) throw new Error(result.error);
  return result.data ?? result.projects ?? [];
}

/** Attach a conversation to a project, scoping its files and knowledge. */
export function loadProject(name: string, contextId: string) {
  return apiRequest<{ ok?: boolean; error?: string }>("projects", {
    body: { action: "load", name, context_id: contextId },
  });
}

/* ---------- agent profiles ---------- */

export interface AgentProfileDetail {
  /** The endpoint returns `id`; `load` takes it back as `profile_id`. */
  id: string;
  title?: string;
  description?: string;
  prompts?: Record<string, string>;
  [key: string]: unknown;
}

export async function listEditableProfiles(): Promise<AgentProfileDetail[]> {
  const result = await apiRequest<{ ok?: boolean; profiles?: AgentProfileDetail[]; data?: AgentProfileDetail[]; error?: string }>(
    "plugins/_agent_editor/agent_editor",
    { body: { action: "list" } },
  );
  if (result.error) throw new Error(result.error);
  return result.profiles ?? result.data ?? [];
}

export function loadProfile(profileId: string) {
  return apiRequest<{ ok?: boolean; error?: string; profile?: AgentProfileDetail }>(
    "plugins/_agent_editor/agent_editor",
    { body: { action: "load", profile_id: profileId } },
  );
}

/** Upload an avatar for a profile. Staged server-side, not applied directly. */
export function uploadProfileAvatar(file: File) {
  const form = new FormData();
  form.append("avatar", file);
  return apiUpload<{ ok?: boolean; error?: string; path?: string }>(
    "plugins/_agent_editor/agent_editor_avatar",
    form,
  );
}

/* ---------- skills import ---------- */

/**
 * Run something that needs a conversation, from a screen that has none.
 *
 * Skill import resolves its project and agent-profile scope from a context, so
 * the endpoints refuse without one. Importing from the administration screen is
 * an instance-wide action, so a scratch conversation gives it the default scope
 * and is removed afterwards -- including if the operation fails, which is why
 * the cleanup is in a finally.
 */
export async function withScratchContext<T>(run: (contextId: string) => Promise<T>): Promise<T> {
  const created = await apiRequest<{ ok?: boolean; ctxid?: string }>("chat_create", {
    body: { current_context: "" },
  });
  const contextId = created.ctxid;
  if (!contextId) throw new Error("Could not create a working context for this operation.");
  try {
    return await run(contextId);
  } finally {
    try {
      await apiRequest<unknown>("chat_remove", { body: { context: contextId } });
    } catch {
      // A leftover empty conversation is untidy, not a failure worth surfacing
      // over whatever the caller was actually doing.
    }
  }
}

export interface SkillImportPreview {
  ok?: boolean;
  error?: string;
  skills?: Array<{ name?: string; description?: string; [key: string]: unknown }>;
  conflicts?: string[];
}

/**
 * Read an archive of skills without installing any.
 *
 * A skill is instructions the agent will follow, so it is worth seeing what an
 * archive contains — and which existing skills it would replace — before it is
 * applied.
 */
export function previewSkillImport(file: File, contextId: string) {
  const form = new FormData();
  form.append("skills_file", file);
  // Both endpoints require a conversation id and refuse without one: an import
  // is scoped to a context and project, not to the instance.
  form.append("ctxid", contextId);
  return apiUpload<SkillImportPreview>("skills_import_preview", form);
}

/**
 * Install skills from an archive.
 *
 * `conflict` decides what happens to a skill of the same name that already
 * exists. It defaults to "skip" server-side; sent explicitly so the choice is
 * visible at the call site rather than inherited.
 */
export function importSkills(
  file: File,
  contextId: string,
  conflict: "skip" | "overwrite" = "skip",
) {
  const form = new FormData();
  form.append("skills_file", file);
  form.append("ctxid", contextId);
  form.append("conflict", conflict);
  return apiUpload<{ success?: boolean; error?: string; imported?: string[] }>(
    "skills_import",
    form,
  );
}

/** Which targets a skill scan can run against. Answers with `success`, not `ok`. */
/**
 * Remove a skill by its path.
 *
 * Requires `plugins.manage`: a skill is an instruction the agent loads on
 * demand, so deleting one changes what every agent on the instance can do. The
 * server enforces that -- hiding the control without the permission is only so
 * the screen does not offer an action that would fail.
 */
export async function deleteSkill(skillPath: string): Promise<void> {
  const result = await apiRequest<{ ok: boolean; error?: string }>("skills", {
    body: { action: "delete", skill_path: skillPath },
  });
  if (!result.ok) throw new Error(result.error || "Could not delete that skill.");
}

export function listScanTargets() {
  return apiRequest<{
    success?: boolean;
    target_type?: string;
    target_label?: string;
    targets?: Array<{ name?: string; path?: string; [key: string]: unknown }>;
    error?: string;
  }>("skills_scan", { body: { action: "targets" } });
}

/* ---------- per-conversation model override ---------- */

export interface ModelOverride {
  provider?: string;
  name?: string;
  [key: string]: unknown;
}

/**
 * The model this conversation uses, if it differs from the instance default.
 *
 * Returns no `ok` field -- `override: null` means "using the default", which is
 * an answer rather than a failure.
 */
export function getModelOverride(contextId: string) {
  return apiRequest<{
    override?: ModelOverride | null;
    allowed?: boolean;
    configured_preset?: string;
    effective_preset?: string;
    error?: string;
  }>("plugins/_model_config/model_override", {
    body: { context_id: contextId, action: "get" },
  });
}

/** Use a named preset for this conversation only. */
export function setModelOverridePreset(contextId: string, presetName: string) {
  return apiRequest<{ ok?: boolean; error?: string }>(
    "plugins/_model_config/model_override",
    { body: { context_id: contextId, action: "set_preset", preset_name: presetName } },
  );
}

export function clearModelOverride(contextId: string) {
  return apiRequest<{ ok?: boolean; error?: string }>(
    "plugins/_model_config/model_override",
    { body: { context_id: contextId, action: "clear" } },
  );
}

/* ---------- document editor ---------- */

export interface OpenDocument {
  path?: string;
  title?: string;
  dirty?: boolean;
  [key: string]: unknown;
}

/** Documents open in this conversation. Returned under `open_files`. */
export async function listOpenDocuments(contextId: string): Promise<OpenDocument[]> {
  const result = await apiRequest<{ ok?: boolean; open_files?: OpenDocument[]; error?: string }>(
    "plugins/_editor/editor_session",
    { body: { action: "list", ctxid: contextId } },
  );
  if (result.error) throw new Error(result.error);
  return result.open_files ?? [];
}

/* ---------- scheduled task creation ---------- */

export interface NewTask {
  name: string;
  prompt: string;
  system_prompt?: string;
  schedule?: Record<string, unknown>;
  timezone: string;
}

export function createScheduledTask(task: NewTask) {
  return apiRequest<{ ok?: boolean; success?: boolean; error?: string; task?: unknown }>(
    "scheduler_task_create",
    { body: task },
  );
}

/* ---------- MCP server scan ---------- */

/**
 * Inspect an MCP server definition before trusting it.
 *
 * The two allow flags default to false on purpose: scanning a server should not
 * itself run the thing being scanned, nor reach the network, unless that is
 * asked for.
 */
export function scanMcpServer(
  server: Record<string, unknown>,
  options: { allowLocalExecution?: boolean; allowRemoteNetwork?: boolean } = {},
) {
  return apiRequest<{ ok?: boolean; success?: boolean; error?: string; report?: unknown }>(
    "mcp_server_scan",
    {
      body: {
        server,
        allow_local_execution: Boolean(options.allowLocalExecution),
        allow_remote_network: Boolean(options.allowRemoteNetwork),
        inspect_runtime: true,
      },
    },
  );
}

/* ---------- workdir structure ---------- */

/** The directory tree as the agent is told about it, with the same limits. */
export function getWorkdirStructure(settings: {
  workdir_path: string;
  workdir_max_depth?: number;
  workdir_max_files?: number;
  workdir_max_folders?: number;
  workdir_max_lines?: number;
}) {
  return apiRequest<{ data?: string; error?: string }>("settings_workdir_file_structure", {
    body: settings,
  });
}

/* ---------- backup grouping ---------- */

/** A backup's contents grouped by directory, for reviewing what it holds. */
export function previewBackupGrouped(
  includePatterns: string[],
  excludePatterns: string[],
  options: { includeHidden?: boolean; maxDepth?: number; searchFilter?: string } = {},
) {
  return apiRequest<{ success?: boolean; groups?: unknown[]; error?: string }>(
    "backup_preview_grouped",
    {
      body: {
        include_patterns: includePatterns,
        exclude_patterns: excludePatterns,
        include_hidden: options.includeHidden ?? true,
        max_depth: options.maxDepth ?? 3,
        search_filter: options.searchFilter ?? "",
      },
    },
  );
}

/* ---------- files and notifications ---------- */

/** Where a conversation's attachments are kept. */
export function getChatFilesPath(contextId: string) {
  return apiRequest<{ ok?: boolean; path?: string; error?: string }>("chat_files_path_get", {
    body: { ctxid: contextId },
  });
}

/** Endpoint that serves an image from a path the backend has vetted. */
const IMAGE_ENDPOINT = "image_get";

/**
 * Fetch an image as an object URL for display.
 *
 * Not a plain `<img src>`: the endpoint requires the CSRF header like every
 * other, so the bytes are fetched and wrapped. The caller owns the returned URL
 * and must revoke it, or the blob is held for the life of the page.
 */
export function fetchImageUrl(path: string): Promise<string> {
  const query = new URLSearchParams({ path });
  return apiObjectUrl(`${IMAGE_ENDPOINT}?${query.toString()}`);
}

/** Download the same image instead of displaying it. */
export function downloadImage(path: string): Promise<void> {
  const query = new URLSearchParams({ path });
  const name = path.split("/").pop() || "image";
  return apiDownload(`${IMAGE_ENDPOINT}?${query.toString()}`, {
    method: "GET",
    filename: name,
  });
}

export function createNotification(
  type: string,
  title: string,
  message: string,
  detail = "",
) {
  return apiRequest<{ ok?: boolean; error?: string }>("notification_create", {
    body: { type, title, message, detail },
  });
}

/* ---------- lifecycle ---------- */

/**
 * Restart the process.
 *
 * Returns an empty 200 and then the connection drops as the server reloads, so
 * a caller that waits for a body waits forever. Every session is interrupted.
 */
export async function restartInstance(): Promise<void> {
  try {
    await apiRequest<unknown>("restart");
  } catch {
    // The connection dropping mid-response is the expected outcome, not a
    // failure: the process is restarting because we asked it to.
  }
}
