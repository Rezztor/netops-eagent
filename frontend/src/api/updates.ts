/**
 * The installation's update service, for the administrator's Updates page.
 *
 * Both endpoints are gated on `settings.manage` server-side. The host tool asks
 * Rezztor's update service, downloads and installs; the application only reads
 * state, stores an upload, and writes a request. Upload uses XMLHttpRequest so
 * the page can show progress.
 */

import { ApiError, apiDownload, apiRequest, getCsrfToken } from "./client";

export type UpdatePhase = "idle" | "downloading" | "checked" | "installing" | "staged" | "restarting";

export type UpdateStepState = "done" | "running" | "waiting" | "failed";

/** One Debian package an update changes (ADR 0092); `from` is null for a package new to the machine. */
export interface DebianChange {
  name: string;
  from: string | null;
  to: string;
}

/** Debian's updates a package carries since its core release. */
export interface PackageDebian {
  snapshot: string;
  packages: DebianChange[];
  /** The kernel, libc6, systemd or dbus change: the appliance needs a restart afterwards. */
  reboot: boolean;
}

/** What the host did with the Debian updates of the last package that carried them. */
export interface DebianState {
  version: string | null;
  snapshot: string | null;
  /** How many packages changed on this machine. */
  applied: number;
  packages: DebianChange[];
  /** True until the appliance has been restarted. */
  reboot_needed: boolean;
  error: UpdateError | null;
  at: string;
}

export interface UpdatePackage {
  file: string;
  size: number;
  version: string;
  base: string;
  restart: "none" | "application";
  database_change: boolean;
  notes: string[];
  key: string;
  created: string;
  /** Absent from a host older than ADR 0092; null when the package carries no Debian updates. */
  os?: PackageDebian | null;
}

export interface UpdateStep {
  id: string;
  text: string;
  state: UpdateStepState;
}

export interface UpdateError {
  code: string;
  text: string;
}

export interface UpdateResult {
  outcome: "installed" | "rolled_back";
  version: string;
  at: string;
  text: string;
  seconds: number | null;
}

export interface UpdateHistoryEntry {
  at: string;
  version: string;
  by: string;
  outcome: string;
}

/** A support bundle the host made; kept 7 days, at most five. */
export interface SupportBundle {
  file: string;
  size: number;
  at: string;
}

/** One package Rezztor's update service lists after the running version (ADR 0091). */
export interface ListedUpdate {
  version: string;
  base: string;
  min_from: string;
  /** A new core release delivered as a package. */
  bridge: boolean;
  released_at: string;
  security: boolean;
  size: number;
  sha256: string | null; // null until the service has made this installation's copy (entries after the first)
  notes: string[];
  /** When this installation's plan may download it. */
  available_at: string;
  /** Only the first entry can be, and only when the plan allows it now. */
  downloadable: boolean;
  /** The Debian updates it carries, when the service says so. */
  os?: { packages: number; reboot: boolean } | null;
}

/**
 * What the host last heard from the update service. Null until it has asked once.
 * After a failed question `plan`, `licence` and `core` are null, `available` is
 * empty and `error` says why.
 */
export interface UpdatesList {
  checked_at: string | null;
  server: string;
  plan: "paid" | "free" | null;
  licence: "active" | "lapsed" | "none" | null;
  core: string | null;
  /** Oldest first, without what is installed. */
  available: ListedUpdate[];
  /**
   * `not_a_release`, `not_installed`, `no_key`, `unreachable`, `unknown_installation`,
   * `bad_answer`, `bad_challenge`, `bad_service_answer`, `service_busy`, `service`, `chain_gap`.
   */
  error: UpdateError | null;
}

/** Update to latest: how far the host has walked the list. */
export interface UpdateProgress {
  done: number;
  total: number;
  current: string;
}

/** While the phase is `downloading`. */
export interface UpdateDownload {
  version: string;
  received: number;
  size: number;
}

export interface UpdateInstalled {
  version: string;
  base: string;
  updated_at: string | null;
}

/** No host update service (developer machine, hand install). */
export interface UpdateUnavailable {
  available: false;
}

export interface UpdateAvailable {
  available: true;
  phase: UpdatePhase;
  installed: UpdateInstalled;
  update_key: string;
  keys: string[];
  package: UpdatePackage | null;
  steps: UpdateStep[];
  error: UpdateError | null;
  result: UpdateResult | null;
  history: UpdateHistoryEntry[];
  request: string | null;
  at: string;
  /** Absent on a host whose update tool has no support bundle yet. */
  bundles?: SupportBundle[];
  bundle_error?: string | null;
  /** Absent on a host whose update tool does not ask the update service. */
  updates?: UpdatesList | null;
  progress?: UpdateProgress | null;
  download?: UpdateDownload | null;
  /** Debian's updates of the last package that carried them (ADR 0092). */
  os?: DebianState | null;
  /** The instance's daily-check switch. */
  daily_check?: boolean;
}

export type UpdateStatus = UpdateUnavailable | UpdateAvailable;

export interface UpdateActionResult {
  ok: boolean;
  request?: string;
  error?: string;
}

export async function fetchUpdateStatus(): Promise<UpdateStatus> {
  return apiRequest<UpdateStatus>("update_status", { body: {} });
}

export function requestUpdate(
  action: "install" | "restart" | "discard" | "bundle" | "refresh" | "update_all" | "reboot",
) {
  return apiRequest<UpdateActionResult>("update_apply", { body: { action } });
}

/** Ask the host to download the first listed package; it is checked like an upload once it is here. */
export function downloadUpdate(version: string) {
  return apiRequest<UpdateActionResult>("update_apply", { body: { action: "download", version } });
}

export function setDailyCheck(enabled: boolean) {
  return apiRequest<UpdateActionResult & { daily_check?: boolean }>("update_apply", {
    body: { action: "daily_check", enabled },
  });
}

export function downloadSupportBundle(file: string): Promise<void> {
  return apiDownload(`support_bundle_download?file=${encodeURIComponent(file)}`, { filename: file });
}

/**
 * Upload a `.rezupd` file. Reports progress through `onProgress` (0–100).
 *
 * Uses XMLHttpRequest rather than `apiUpload`: fetch cannot report upload
 * progress, and a multi-hundred-MB package needs a bar.
 */
export function uploadUpdate(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UpdateActionResult> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("action", "upload");
    form.append("package", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/update_apply");
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    const csrf = getCsrfToken();
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);

    xhr.upload.onprogress = (event) => {
      if (!onProgress || !event.lengthComputable || event.total <= 0) return;
      onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
    };

    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = xhr.responseText;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const message =
          body && typeof body === "object" && typeof (body as { error?: unknown }).error === "string"
            ? (body as { error: string }).error
            : typeof body === "string" && body.trim()
              ? body.trim()
              : `Request failed with status ${xhr.status}`;
        reject(new ApiError("update_apply", xhr.status, message, body));
        return;
      }
      resolve((body ?? { ok: false, error: "Empty answer." }) as UpdateActionResult);
    };

    xhr.onerror = () => {
      reject(new ApiError("update_apply", 0, "The upload could not be completed.", null));
    };

    xhr.send(form);
  });
}
