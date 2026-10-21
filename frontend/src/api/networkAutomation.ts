/**
 * The Ansible executor's address, and the changes waiting for an engineer.
 *
 * Two endpoints with two different permissions, deliberately kept apart:
 * configuring the executor is `plugins.manage`, approving the commands a change
 * would push is `network.approve`. An administrator holds both; a future
 * network-approver role would hold only the second.
 *
 * The executor tokens are never sent to this screen. `executor_token_set` and
 * `admin_token_set` say whether each exists; the fields start blank on every
 * load, and leaving one blank on save keeps whatever is stored.
 *
 * `*_token_origin` says where each one comes from, which matters because the
 * two sources override in the direction nobody expects: a value saved here is
 * written to usr/.env and beats the one the deployment injects. Showing the
 * origin is what stops someone typing over a deployment-injected value and creating a
 * copy that silently outlives the rotation of the original.
 */

import { apiRequest } from "./client";

export type TokenOrigin = "file" | "environment" | "unset";

export interface NetworkConfig {
  executor_url: string;
  executor_token_key: string;
  executor_token_set: boolean;
  admin_token_key?: string;
  admin_token_set?: boolean;
  /** "file" (saved here), "environment" (injected by the deployment), "unset". */
  executor_token_origin?: TokenOrigin;
  admin_token_origin?: TokenOrigin;
  configured: boolean;
  job_timeout_seconds: number;
  max_devices_per_job: number;
  /** How strictly a fixed read or bundle asks for a card; free-form commands always ask (ADR 0048). */
  read_approval?: ReadApproval;
  /** Refuse device-data tools unless every model the result could reach is local (ADR 0052). */
  require_local_model?: boolean;
}

export type ReadApproval = "every_time" | "once_per_chat" | "log_only";

/**
 * What the executor enforces, reported by it rather than configured here. A
 * screen that let an administrator type a rate limit the executor does not read
 * would be showing them a number nothing applies.
 */
export interface ExecutorLimits {
  max_concurrent_jobs?: number;
  max_queued_jobs?: number;
  max_jobs_per_minute?: number;
}

export interface NetworkState {
  ok: boolean;
  plugin_enabled: boolean;
  config: NetworkConfig;
  /** Only present after a reachability test. */
  reachable?: boolean;
  detail?: string;
  catalogue_commit?: string;
  catalogue_entries?: string[];
  busy?: boolean;
  limits?: ExecutorLimits;
  queue?: { running?: number; queued?: number };
  collections?: Record<string, string>;
  ansible_version?: string;
  error?: string;
}

/** A change as the register lists it. Not enough to approve from — see ChangeDetail. */
export interface ChangeSummary {
  id: string;
  status: string;
  playbook_name: string;
  summary: string;
  limit: string;
  created_at: number;
  requested_by: string;
  approved_by: string;
  /** Present on detail (`to_dict`); list summaries may omit it until approved. */
  approved_at?: number | null;
  command_count: number;
  render_method: string;
}

/** The whole plan. This is what an approver actually reads. */
export interface ChangeDetail extends ChangeSummary {
  commands: string[];
  rollback_commands: string[];
  rollback_name: string;
  variables: Record<string, unknown>;
  commit: string;
  approved_hash: string;
  current_hash: string;
  error: string;
  /** What a change-high plan makes the approver type; "" or absent when none. */
  confirm?: string;
}

export async function getNetworkConfig(): Promise<NetworkState> {
  const result = await apiRequest<NetworkState>("plugins/_ea_ansible/ansible_config", {
    body: { action: "get" },
  });
  if (!result.ok) throw new Error(result.error || "Could not read the network automation settings.");
  return result;
}

export async function saveNetworkConfig(
  config: Partial<NetworkConfig>,
  executorToken?: string,
  adminToken?: string,
): Promise<NetworkState> {
  const result = await apiRequest<NetworkState>("plugins/_ea_ansible/ansible_config", {
    body: {
      action: "save",
      config,
      ...(executorToken ? { executor_token: executorToken } : {}),
      ...(adminToken ? { admin_token: adminToken } : {}),
    },
  });
  if (!result.ok) throw new Error(result.error || "Could not save the network automation settings.");
  return result;
}

/**
 * Asks the executor what it is. A success means this instance can reach it and
 * which catalogue it is pinned to — not that any device is reachable.
 */
export async function testNetworkExecutor(): Promise<NetworkState> {
  return apiRequest<NetworkState>("plugins/_ea_ansible/ansible_config", {
    body: { action: "test" },
  });
}

export async function getChangesAwaitingApproval() {
  return apiRequest<{ ok: boolean; changes?: ChangeSummary[]; error?: string }>(
    "plugins/_ea_ansible/changes_review",
    { body: {} },
  );
}

/** A row on the Approvals page: something the caller may sign, oldest first (`approvals_waiting`). */
export interface ApprovalWaiting extends ChangeSummary {
  kind: string;
  expires_at: number | null;
  tier: string;
  /** What a change-high card makes the approver type; "" when none. */
  confirm: string;
  /** Decided on its chat card (CR/SR number and document), not here. */
  azure: boolean;
  requested_by_name: string;
  /** The caller's own records only: the conversation that holds the card. */
  context_id: string;
}

export async function getApprovalsWaiting() {
  const result = await apiRequest<{ ok: boolean; changes?: ApprovalWaiting[]; error?: string }>(
    "plugins/_ea_ansible/approvals_waiting",
    { body: {} },
  );
  if (!result.ok) throw new Error(result.error || "Could not read what waits for approval.");
  return result.changes ?? [];
}

export async function getChangeForReview(changeId: string) {
  return apiRequest<{ ok: boolean; change?: ChangeDetail; error?: string }>(
    "plugins/_ea_ansible/changes_review",
    { body: { change_id: changeId } },
  );
}

/**
 * Approve or reject a change. Approving binds to the commands as they stand
 * now: if the devices move afterwards the change is refused as superseded
 * rather than applied against a plan nobody read.
 */
export async function decideChange(
  changeId: string,
  decision: "approve" | "reject",
  /** The `current_hash` of the plan the person read. The server refuses an approval when what would run is no longer that. */
  expectedHash = "",
  /** What a change-high card makes the approver type (the device name, or `apply to N devices`). */
  confirmation = "",
) {
  const result = await apiRequest<{ ok: boolean; change?: ChangeDetail; error?: string }>(
    "plugins/_ea_ansible/change_approve",
    {
      body: {
        change_id: changeId,
        decision,
        ...(decision === "approve" && expectedHash ? { expected_hash: expectedHash } : {}),
        ...(decision === "approve" && confirmation ? { confirmation } : {}),
      },
    },
  );
  if (!result.ok) throw new Error(result.error || "Could not record that decision.");
  return result;
}
