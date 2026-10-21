/**
 * The network console's reads and its two writes.
 *
 * Kept apart from `networkAutomation.ts` by permission rather than by subject.
 * That file is the settings pane — `plugins.manage`, which chooses the executor.
 * This one is the operational console: reading what ran is `network.view`, which
 * an engineer needs and an administrator's permission should not be required for.
 *
 * The inventory endpoint is the exception and stays `plugins.manage`, because
 * choosing which devices the agent can reach is the same kind of decision as
 * choosing which executor it talks to.
 *
 * A credential travels one way. The executor has no endpoint that returns a
 * stored secret, so neither does this: what comes back is a fingerprint, which
 * says whether the value you are holding is the value that is loaded without
 * being it.
 */

import { apiRequest } from "./client";

export interface JobSummary {
  job_id: string;
  source: string;
  actor: string;
  playbook_name: string;
  commit: string;
  limit: string;
  check_mode: boolean;
  status: string;
  exit_code: number | null;
  started_at: number;
  finished_at: number | null;
  error: string;
  change_id: string;
  event_count: number;
}

/** One task's result as the module reported it. This is what an approver reads. */
export interface JobEvent {
  task?: string;
  host?: string;
  commands?: string[];
  rendered?: string;
  diff?: unknown;
  changed?: boolean;
}

export interface JobDetail extends Omit<JobSummary, "event_count"> {
  events: JobEvent[];
  output: string;
  variables: Record<string, unknown>;
}

export interface InventoryHost {
  ansible_host: string;
  vars: Record<string, unknown>;
}

export interface InventoryGroup {
  hosts: string[];
  vars: Record<string, unknown>;
  /** The credential's *name*. The value lives on the executor and comes back only as a fingerprint. */
  credential: string;
}

export interface InventoryDocument {
  groups: Record<string, InventoryGroup>;
  hosts: Record<string, InventoryHost>;
}

export interface CredentialSummary {
  name: string;
  kind: string;
  username: string;
  fingerprint: string;
  updated_at: number | null;
}

export async function listJobs(params: {
  limit?: number;
  offset?: number;
  status?: string;
  source?: string;
  playbook?: string;
} = {}) {
  return apiRequest<{ ok: boolean; jobs?: JobSummary[]; total?: number; error?: string }>(
    "plugins/_ea_ansible/network_jobs",
    { body: params },
  );
}

export async function getJob(jobId: string) {
  return apiRequest<{ ok: boolean; job?: JobDetail; error?: string }>(
    "plugins/_ea_ansible/network_jobs",
    { body: { job_id: jobId } },
  );
}

export async function getInventory() {
  return apiRequest<{
    ok: boolean;
    inventory?: InventoryDocument;
    unresolved_credentials?: string[];
    credentials?: CredentialSummary[];
    error?: string;
  }>("plugins/_ea_ansible/network_inventory", { body: { action: "get" } });
}

export async function saveInventory(inventory: InventoryDocument) {
  const result = await apiRequest<{
    ok: boolean;
    inventory?: InventoryDocument;
    unresolved_credentials?: string[];
    error?: string;
  }>("plugins/_ea_ansible/network_inventory", { body: { action: "save_inventory", inventory } });
  if (!result.ok) throw new Error(result.error || "The executor did not accept that inventory.");
  return result;
}

/** Blank secrets are refused rather than treated as "clear it". Clearing is deleting. */
export async function saveCredential(input: {
  name: string;
  kind: string;
  secret: string;
  username?: string;
}) {
  const result = await apiRequest<{
    ok: boolean;
    credential?: CredentialSummary;
    error?: string;
  }>("plugins/_ea_ansible/network_inventory", { body: { action: "save_credential", ...input } });
  if (!result.ok) throw new Error(result.error || "Could not store that credential.");
  return result;
}

export async function deleteCredential(name: string) {
  const result = await apiRequest<{ ok: boolean; error?: string }>(
    "plugins/_ea_ansible/network_inventory",
    { body: { action: "delete_credential", name } },
  );
  if (!result.ok) throw new Error(result.error || "Could not delete that credential.");
  return result;
}

/** One network of the appliance, as the console wrote it. Read-only: networks are changed on the appliance console. */
export interface ApplianceNetwork {
  name: string;
  roles: string[];
  interface: string;
  parent: string | null;
  vlan: number | null;
  /** The appliance's own address here, with its prefix. */
  address: string;
  gateway: string | null;
  subnet: string;
  routed_subnets: string[];
  state: "up" | "down";
}

export interface NetworkList {
  ok: boolean;
  /** False when the appliance has not said which networks it is on: one flat network, nothing known. */
  known: boolean;
  generated_at: number | null;
  networks: ApplianceNetwork[];
  /** "On your devices, allow management from 10.0.100.5 (VLAN 100)." */
  hint: string;
  /** Address -> "VLAN 200, routed", for the addresses asked about. */
  labels: Record<string, string>;
}

export async function getNetworks(addresses: string[] = []) {
  return apiRequest<NetworkList>("plugins/_ea_ansible/network_list", { body: { addresses } });
}
