/**
 * The installation's licence, for the administrator's licence page.
 *
 * Both endpoints are gated on `settings.manage` server-side
 * (api/license_status.py, api/license_activate.py) and return the same
 * `LicenceStatus`, so an action re-renders the page without a second round
 * trip. Times are seconds since 1970 (UTC), as the signed documents hold them.
 *
 * `getLicenceStatus` makes no call beyond this installation: the verdict is
 * worked out from the signed documents it holds. Only the actions talk to the
 * licence server. The activation key goes one way; no answer carries it or the
 * installation token.
 */

import { apiRequest } from "./client";

export type LicenceState = "community" | "licensed" | "grace1" | "grace2" | "restricted";

/** What was bought, from the signed licence. */
export interface LicenceDocument {
  lic: string;
  ver: number;
  customer: string;
  kind: string;
  edition: string;
  features: string[];
  seats: number;
  instances: number;
  /** True for a licence loaded from a file: no leases, no calls to the server. */
  offline: boolean;
  contract_end: number;
  grace_days: number;
}

export interface LicenceStatus {
  state: LicenceState;
  /** A short code: ok, revoked_notice, ended, no_licence, invalid, no_lease, ... */
  reason: string;
  /** One plain sentence when there is something to say. */
  message: string | null;
  /** The rule a stored document broke, when one did. */
  problem: string;
  /** This server's clock reads earlier than the latest time the licence saw. */
  clock_behind: boolean;
  agent_runs_allowed: boolean;
  /** When the installation becomes restricted if nothing changes. */
  restricted_at: number | null;
  days_left: number | null;
  licence: LicenceDocument | null;
  lease_end: number | null;
  /** End of the notice period of a revoked licence. */
  notice_until: number | null;
  seats_used: number;
  /** Active inventory devices plus Azure firewalls, load balancers and gateways. */
  devices_used: number;
  /** The licence's device limit: its signed `devices`, else its edition's; null for no limit. */
  devices_allowed: number | null;
  /** "near" from 90% of the limit, "over" above it. A warning only: nothing is refused. */
  devices_warning: "near" | "over" | null;
  can_release: boolean;
  /** A request that has had no verified answer yet and is being repeated. */
  pending: "activate" | "checkin" | "deactivate" | null;
  /** Previous licence id while its release from this installation is still being confirmed. */
  releasing?: string | null;
  /** Set on the activate answer when that previous licence was released. */
  released_lic?: string | null;
  last_attempt_at: number | null;
  last_success_at: number | null;
  last_result: string | null;
  next_attempt_at: number | null;
  installation_id: string;
  server_url: string;
}

export interface LicenceActionResult {
  ok: boolean;
  error?: string;
  licence?: LicenceStatus;
  request_code?: string;
}

export async function getLicenceStatus(): Promise<LicenceStatus> {
  const result = await apiRequest<LicenceActionResult>("license_status");
  if (!result.ok || !result.licence) {
    throw new Error(result.error || "Could not load the licence status.");
  }
  return result.licence;
}

function action(body: Record<string, unknown>) {
  return apiRequest<LicenceActionResult>("license_activate", { body });
}

/** Exchange an activation key for a licence. The key's check character is verified before any call leaves. */
export function activateLicence(key: string) {
  return action({ action: "activate", key });
}

export function checkInLicence() {
  return action({ action: "checkin" });
}

/** Free this installation's slot, for a move to another server. The caller has confirmed. */
export function releaseInstallation() {
  return action({ action: "deactivate", confirm: true });
}

/** Load an offline licence file. It is verified for this installation before it is stored. */
export function installLicenceFile(licence: string) {
  return action({ action: "install", licence });
}

/** The request code that names this installation to Rezztor. No network call. */
export function requestActivationCode(deploymentName: string) {
  return action({ action: "request_code", deployment_name: deploymentName });
}
