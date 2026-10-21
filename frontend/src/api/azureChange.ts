/**
 * Azure changes (plugins/_ea_azure_change): the list for the Changes tab and the administrator's guard rails, keys and
 * role test. Nothing here changes anything in Azure, and no key ever comes back: `make_key` answers with the public
 * certificate once, and `keys` only says what is set.
 *
 * Handlers: azure_changes (inventory.view) and azure_change_config (plugins.manage), which proxies to the cloud
 * executor's /v1/admin/*. The body shapes below follow docs/plans/2026-10-03-azure-operations-build.md, "Endpoints".
 */

import { ApiError, apiRequest } from "./client";

const API = "plugins/_ea_azure_change/";
const call = <T>(handler: string, body: unknown = {}) => apiRequest<T>(`${API}${handler}`, { body });

export interface AzureChangeDocument {
  version: "issued" | "applied" | "runbook";
  format: "pdf" | "docx";
  created_at?: string | number;
  /** Whether the requester has fetched this document (the approval needs it); the register sends a flag, not the time. */
  downloaded?: boolean;
  sha256?: string;
}

export interface AzureChangeSummary {
  id: string;
  status: string;
  kind: string;
  playbook_name: string;
  summary: string;
  limit: string;
  created_at: number;
  requested_by: string;
  approved_by: string;
  /** The CR or SR number typed at approval; empty until then. */
  reference: string;
  plan_digest: string;
  documents?: AzureChangeDocument[];
}

/**
 * The list. A switched-off plugin still answers (its handlers are always served), with `enabled: false`; with nothing to
 * show that is reported as a 404, which the Changes tab reads as "absent" and draws nothing.
 */
export async function getAzureChanges(reference = ""): Promise<AzureChangeSummary[]> {
  const result = await call<{ ok: boolean; enabled?: boolean; changes?: AzureChangeSummary[]; error?: string }>("azure_changes", {
    ...(reference.trim() ? { reference: reference.trim() } : {}),
  });
  if (!result.ok) throw new Error(result.error || "Could not list the Azure changes.");
  if (result.enabled === false && !result.changes?.length) {
    throw new ApiError(`${API}azure_changes`, 404, "Azure changes are switched off.", result);
  }
  return result.changes ?? [];
}

/** What the executor stores and enforces. There is no field for deleting, stopping or restarting: that is not configurable. */
export interface Guardrails {
  subscriptions: string[];
  resource_groups: { existing: string[]; new_prefix: string };
  regions: string[];
  resource_types: string[];
  vm_sizes_max_vcpu: number;
  usd_month_per_change: number;
  usd_month_total: number;
  low_risk_usd_month: number;
  max_resources: number;
  allow_free_code: boolean;
  allow_unknown_price: boolean;
  reference_pattern: string;
  document_valid_days: number;
}

export type KeyName = "planner" | "applier";
export interface KeyState {
  set: boolean;
  thumbprint?: string;
  expires_on?: string;
  client_id?: string;
}
export interface KeysState {
  planner: KeyState;
  applier: KeyState;
  passphrase_set: boolean;
}

export interface RoleTest {
  ok: boolean;
  role_ok?: boolean;
  /** Why the change app's role was refused, in the executor's words. */
  reason?: string;
  error?: string;
}

const must = <T extends { ok: boolean; error?: string }>(result: T, fallback: string): T => {
  if (!result.ok) throw new Error(result.error || fallback);
  return result;
};

export async function getGuardrails(): Promise<Guardrails> {
  const result = must(
    await call<{ ok: boolean; guardrails: Guardrails; error?: string }>("azure_change_config", { action: "get" }),
    "Could not read the guard rails.",
  );
  return result.guardrails;
}

export async function saveGuardrails(guardrails: Guardrails): Promise<Guardrails> {
  const result = must(
    await call<{ ok: boolean; guardrails: Guardrails; error?: string }>("azure_change_config", {
      action: "save_guardrails",
      guardrails,
    }),
    "The guard rails were not saved.",
  );
  return result.guardrails;
}

export async function getKeys(): Promise<KeysState> {
  const result = must(
    await call<{ ok: boolean; keys?: KeysState; error?: string } & Partial<KeysState>>("azure_change_config", {
      action: "keys",
    }),
    "Could not read the keys.",
  );
  return (result.keys ?? result) as KeysState;
}

export async function makeKey(which: KeyName, tenantId: string, clientId: string) {
  return must(
    await call<{ ok: boolean; public_certificate: string; thumbprint: string; expires_on: string; error?: string }>(
      "azure_change_config",
      { action: "make_key", role: which, tenant_id: tenantId, client_id: clientId },
    ),
    "The key was not made.",
  );
}

export async function testRole(): Promise<RoleTest> {
  return call<RoleTest>("azure_change_config", { action: "test" });
}
