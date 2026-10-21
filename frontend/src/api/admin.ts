/**
 * Administration endpoints: users, plugins, settings.
 *
 * Every call here needs a permission the backend enforces in
 * helpers/api_policy.py. The frontend hides these screens from accounts that
 * lack the permission, but that is a courtesy -- the endpoints reject the
 * request either way, and a 403 from here is expected, not exceptional.
 */

import { apiAction, apiRequest } from "./client";
import type { Role } from "./types";

/* ---------- users ---------- */

export interface AdminUser {
  id: string;
  username: string;
  display_name: string;
  email: string;
  role: Role;
  disabled: boolean;
  created_at: string;
  created_by: string;
  last_login: string | null;
  must_change_password: boolean;
}

export interface RoleInfo {
  id: Role;
  title: string;
  description: string;
  permissions: string[];
}

export interface UsersListResponse {
  ok: boolean;
  users: AdminUser[];
  roles: RoleInfo[];
}

export function listUsers(): Promise<UsersListResponse> {
  return apiRequest<UsersListResponse>("users_list");
}

export interface CreateUserInput {
  username: string;
  password?: string;
  role: Role;
  display_name?: string;
  email?: string;
}

export function createUser(input: CreateUserInput) {
  return apiAction<{ ok: boolean; user: AdminUser; generated_password?: string; error?: string }>(
    "users_create",
    { body: input },
  );
}

/**
 * Update a user.
 *
 * Keyed on `username`, not `id` -- that is what api/users_update.py reads.
 * Omitted fields are left unchanged, so this takes a partial deliberately.
 * A generated password is returned once as `generated_password`.
 */
export interface UpdateUserInput {
  username: string;
  role?: Role;
  display_name?: string;
  email?: string;
  disabled?: boolean;
  password?: string;
  generate_password?: boolean;
}

export function updateUser(input: UpdateUserInput) {
  return apiAction<{
    ok: boolean;
    user?: AdminUser;
    generated_password?: string;
    error?: string;
  }>("users_update", { body: input });
}

export function deleteUser(username: string) {
  return apiAction<{ ok: boolean }>("users_delete", { body: { username } });
}

/** Signed-in user changes their own password (requires the current one). */
export function changePassword(input: { current_password: string; new_password: string }) {
  return apiAction<{ ok: boolean; error?: string }>("change_password", { body: input });
}

/* ---------- plugins ---------- */

export interface PluginListItem {
  /** Mirrors helpers/plugins.py PluginListItem (pydantic model_dump). */
  name: string;
  path: string;
  display_name?: string;
  description?: string;
  version?: string;
  author?: string;
  repo?: string;
  always_enabled?: boolean;
  is_custom?: boolean;
  has_main_screen?: boolean;
  has_config_screen?: boolean;
  has_readme?: boolean;
  has_license?: boolean;
  has_execute_script?: boolean;
  /** "enabled" | "disabled", resolved from the global/root toggle state. */
  toggle_state?: "enabled" | "disabled";
  current_commit?: string;
  current_commit_timestamp?: string;
  thumbnail_url?: string;
}

export function listPlugins(filter: { builtin?: boolean; custom?: boolean; search?: string } = {}) {
  return apiRequest<{ ok: boolean; plugins: PluginListItem[] }>("plugins_list", { body: filter });
}

export function togglePlugin(name: string, enabled: boolean) {
  return apiAction<{ ok: boolean }>("plugins", {
    body: { action: "toggle_plugin", plugin_name: name, enabled },
  });
}

/**
 * Read a plugin's bundled README or LICENSE.
 *
 * The inherited interface's plugin `webui/` layer was removed in this fork
 * (decision 0015), so `has_config_screen`/`has_main_screen` never resolve to a
 * page here. The Markdown docs shipped in each plugin directory do survive, and
 * `api/plugins.py::_get_doc` serves them -- which is the only in-app "deep link"
 * a plugin can still offer.
 */
export function getPluginDoc(name: string, doc: "readme" | "license") {
  return apiRequest<{ ok: boolean; content: string; filename: string }>("plugins", {
    body: { action: "get_doc", plugin_name: name, doc },
  });
}

/* ---------- settings ---------- */

export interface SettingsResponse {
  settings: Record<string, unknown>;
  additional: {
    chat_providers?: unknown[];
    embedding_providers?: unknown[];
    is_dockerized?: boolean;
    agent_subdirs?: Array<{ value: string; label: string }>;
    knowledge_subdirs?: Array<{ value: string; label: string }>;
    timezones?: unknown[];
    resolved_timezone?: string;
    runtime_settings?: Record<string, unknown>;
  };
}

export function getSettings() {
  return apiRequest<SettingsResponse>("settings_get");
}

export function saveSettings(settings: Record<string, unknown>) {
  return apiAction<SettingsResponse>("settings_set", { body: settings });
}

/* ---------- health ---------- */

export interface HealthResponse {
  gitinfo?: { version?: string; commit_time?: string };
  error?: string | null;
}

export function getHealth() {
  return apiRequest<HealthResponse>("health", { method: "GET" });
}
