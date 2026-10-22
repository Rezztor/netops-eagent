/**
 * Types for the backend API.
 *
 * `BootstrapResponse` mirrors the pydantic model in `api/bootstrap.py`, which is
 * published in the OpenAPI document at `GET /api/openapi`. It is hand-written
 * rather than generated because it is currently the only endpoint that declares
 * a body schema (ADR 0009). When more endpoints declare
 * models, generate this file from the document instead of extending it by hand.
 */

export type Role = "admin" | "power" | "user";

/** Permission strings from helpers/rbac.py. */
export const PERMISSIONS = {
  usersManage: "users.manage",
  settingsManage: "settings.manage",
  pluginsManage: "plugins.manage",
  schedulerManage: "scheduler.manage",
  auditViewAll: "audit.view_all",
  chatUse: "chat.use",
  toolCodeExecution: "tools.code_execution",
  toolSubordinates: "tools.subordinates",
  toolNetwork: "tools.network",
  networkApprove: "network.approve",
  networkView: "network.view",
  inventoryView: "inventory.view",
  inventoryManage: "inventory.manage",
  inventoryApprove: "inventory.approve",
  inventoryPlaces: "inventory.places",
} as const;

export type Permission = (typeof PERMISSIONS)[keyof typeof PERMISSIONS];

export interface BootstrapVersion {
  version: string;
  commit_time: string;
}

export interface BootstrapUser {
  id: string;
  username: string;
  display_name: string;
  email: string;
  role: Role;
  role_title: string;
  /** Block the whole app on the change-password screen while true. */
  must_change_password: boolean;
}

export interface BootstrapRuntime {
  runtime_id: string;
  is_development: boolean;
  is_dockerized: boolean;
}

export interface BootstrapPreferences {
  timezone: string;
  time_format: string;
  ui_control_visibility: Record<string, unknown>;
}

/**
 * Licence verdict every signed-in user can see — the app-wide banner's data.
 * The full payload (seats, customer, features) stays on the admin-gated
 * licence_status endpoint; this carries only state and the run gate.
 */
export interface BootstrapLicence {
  state: "community" | "licensed" | "grace1" | "grace2" | "restricted";
  /** False when the installation is restricted. */
  agent_runs_allowed: boolean;
  /** Days until the installation is restricted, in grace1 and grace2 only. */
  grace_days_remaining: number | null;
  /** One plain sentence when there is something to say; null when all is well. */
  reason: string | null;
}

/**
 * Staged update waiting for a restart. Present only for holders of
 * `settings.manage`; null when there is no update service or nothing waits.
 * Optional so an older backend without the field still bootstraps.
 */
export interface BootstrapUpdate {
  phase: string;
  version: string;
}

/**
 * The shape of `GET /api/bootstrap`.
 *
 * Everything after `version` is present only when authenticated, which is why
 * the authenticated fields are optional here and why `AuthenticatedBootstrap`
 * below exists: once `authenticated` is true they are all guaranteed, and the
 * discriminated union lets the app stop null-checking them everywhere.
 */
export interface BootstrapResponse {
  ok: boolean;
  authenticated: boolean;
  version: BootstrapVersion;
  user?: BootstrapUser;
  permissions?: string[];
  runtime?: BootstrapRuntime;
  csrf_token?: string;
  preferences?: BootstrapPreferences;
  webui_extensions?: Record<string, unknown> | null;
  licence?: BootstrapLicence | null;
  update?: BootstrapUpdate | null;
  /** Packages the update service listed when the host last asked; administrators only. */
  updates_available?: number | null;
}

export interface AnonymousBootstrap {
  authenticated: false;
  version: BootstrapVersion;
}

export interface AuthenticatedBootstrap {
  authenticated: true;
  version: BootstrapVersion;
  user: BootstrapUser;
  permissions: string[];
  runtime: BootstrapRuntime;
  csrfToken: string;
  preferences: BootstrapPreferences;
  webuiExtensions: Record<string, unknown> | null;
  /** Null when the verdict could not be computed — the banner just does not appear. */
  licence: BootstrapLicence | null;
  /** Null when nothing waits, or when the backend is older than the update field. */
  update?: BootstrapUpdate | null;
  /** Null without an update service or a list; administrators only. */
  updatesAvailable?: number | null;
}

export type Session = AnonymousBootstrap | AuthenticatedBootstrap;
