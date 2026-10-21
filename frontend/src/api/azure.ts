/**
 * The Azure cloud inventory (plugins/_ea_azure). Read-only: nothing here changes anything in Azure.
 *
 * Handlers: azure_status, azure_tree, azure_resources, azure_resource, azure_history, azure_pull (inventory.*) and
 * azure_config, azure_test (plugins.manage). The credential never comes back from any of them, only
 * `AzureCredential` (kind, whether set, fingerprint, expiry). A refusal that has a reason is returned as a body
 * `{ok: false, error, reason}`; the helpers that cannot go on throw.
 */

import { apiRequest } from "./client";

const API = "plugins/_ea_azure/";
const call = <T>(handler: string, body: unknown = {}) => apiRequest<T>(`${API}${handler}`, { body });

function must<T extends { ok: boolean; error?: string }>(result: T, fallback: string): T {
  if (!result.ok) throw new Error(result.error || fallback);
  return result;
}

export const CATEGORIES = [
  "compute",
  "web",
  "containers",
  "networking",
  "storage",
  "databases",
  "identity_security",
  "integration",
  "monitoring",
  "other",
] as const;
export type CloudCategory = (typeof CATEGORIES)[number];

export const CATEGORY_LABELS: Record<CloudCategory, string> = {
  compute: "Compute",
  web: "Web and apps",
  containers: "Containers",
  networking: "Networking",
  storage: "Storage",
  databases: "Databases",
  identity_security: "Identity and security",
  integration: "Integration",
  monitoring: "Monitoring and management",
  other: "Other",
};

export type CloudState = "running" | "stopped" | "failed" | "ok" | "unknown";
export type Attention = "stopped_failed" | "no_tags" | "exposed" | "changed" | "waste" | "drifted";

export interface AzureRunCounts {
  trigger?: string;
  subscriptions?: { listed: number; read: number; failed: number; skipped: number };
  resources?: number;
  added?: number;
  changed?: number;
  removed?: number;
  returned?: number;
  unknown_types?: string[];
  duration_s?: number;
  per_subscription?: { id: string; name: string; status: "ok" | "failed" | "skipped"; resources: number; error: string }[];
}

export interface AzureRun {
  id: string;
  status: "running" | "succeeded" | "failed" | "dry_run" | "partial";
  started_at: string;
  finished_at: string | null;
  counts: AzureRunCounts | null;
  error: string | null;
  batch_id?: string | null;
  requested_by: string | null;
}

export interface AzureCredential {
  kind: "certificate" | "secret" | "";
  set: boolean;
  fingerprint: string;
  expires_on: string;
}

/** How a live read (azure_read) is approved: a card every time, once per resource per chat, or never (audited). */
export const READ_APPROVALS = ["every_time", "once_per_chat", "log_only"] as const;
export type ReadApproval = (typeof READ_APPROVALS)[number];

/** The log application's settings. Its certificate is never here: only `AzureLogsCredential` says it exists. */
export interface AzureLogsConfig {
  client_id: string;
  workspaces: { name: string; id: string }[];
  tables: string[];
  max_timespan: string;
}

export interface AzureLogsCredential {
  set: boolean;
  fingerprint: string;
  expires_on: string;
}

export interface AzureConfig {
  cloud: string;
  tenant_id: string;
  client_id: string;
  auth: "certificate" | "secret";
  secret_expires_on: string;
  subscriptions_mode: "all" | "only" | "except";
  subscription_ids: string[];
  schedule: { frequency: "off" | "daily" | "weekly"; weekday: string; time: string; timezone: string };
  keep_removed_days: number;
  keep_history_days: number;
  min_pull_interval_minutes: number;
  timeout_seconds: number;
  page_size: number;
  max_resources: number;
  read_approval?: ReadApproval;
  logs?: AzureLogsConfig;
  [key: string]: unknown;
}

export interface AzureAsOf {
  last_pull: AzureRun | null;
  subscriptions: { id?: string; name: string; last_read_at: string | null; readable: boolean }[];
}

export interface AzureStatus {
  ok: boolean;
  enabled: boolean;
  configured: boolean;
  has_data: boolean;
  test_endpoints: boolean;
  running: AzureRun | null;
  last_run: AzureRun | null;
  next_pull_at: string | null;
  as_of: AzureAsOf;
  credential: { kind: string; expires_on: string; expires_in_days: number | null };
  verified_against_real_tenant: boolean;
  error?: string;
}

export interface TreeNode {
  id: string;
  native_id: string;
  name: string;
  state: string;
  region: string;
  tags: Record<string, string>;
  props: { management_groups?: { id: string; name: string }[] } & Record<string, unknown>;
  readable: boolean;
  last_read_at: string | null;
  count: number;
  categories: Partial<Record<CloudCategory, number>>;
}

export interface AzureTree {
  ok: boolean;
  enabled: boolean;
  tree: {
    account: { id: string; native_id: string; name: string } | null;
    subscriptions: (TreeNode & { groups: TreeNode[] })[];
  };
  attention: Record<Attention, number>;
  as_of: AzureAsOf;
  error?: string;
}

export interface CloudLink {
  kind: string;
  to_native_id: string;
  props: Record<string, unknown>;
  /** null when the other end is outside what the account can read. */
  id: string | null;
  name: string;
  type: string | null;
  label: string;
  state: string | null;
}

export interface CloudResource {
  id: string;
  native_id: string;
  name: string;
  type: string;
  kind: string;
  label: string;
  category: CloudCategory;
  subscription: { id: string; name: string };
  resource_group: { id: string; name: string };
  parent: { id: string; name: string } | null;
  region: string;
  state: CloudState;
  state_detail: string;
  sku: Record<string, unknown>;
  tags: Record<string, string>;
  props: Record<string, unknown>;
  first_seen_at: string;
  last_seen_at: string;
  changed_at: string | null;
  removed_at: string | null;
  /** On the children of a resource page (a network's subnets): the links out of each. */
  links_out?: CloudLink[];
}

export interface CloudChange {
  id: string;
  run_id: string;
  at: string;
  change: "added" | "changed" | "removed" | "returned";
  fields: Record<string, { old: unknown; new: unknown }>;
  /** Only in the history of a pull, where the rows are not already under one resource. */
  resource?: {
    id: string;
    name: string;
    type: string;
    label: string;
    category: CloudCategory;
    subscription: string;
    resource_group: string;
    removed_at: string | null;
  };
}

interface Filters {
  q?: string;
  subscription?: string;
  resource_group?: string;
  category?: string;
  type?: string;
  region?: string;
  state?: string;
  tag?: string;
  /** `route_table`, `security_group`, `tags`, or `tag:<name>` for resources that lack that tag. */
  without?: string;
  changed_since?: string;
  include_removed?: boolean;
  attention?: Attention | "";
  limit?: number;
  offset?: number;
}
export type ResourceFilters = { [K in keyof Filters]?: Filters[K] | undefined };

/** A facet is a bare value or a value with its count; the screen takes either. */
export type Facet = string | { value?: string; name?: string; count?: number };

export interface ResourceList {
  ok: boolean;
  resources: CloudResource[];
  total: number;
  facets: { types: { type: string; label: string; count: number }[]; regions: Facet[]; states: Facet[]; tag_names: Facet[] };
  error?: string;
}

/** What Azure itself reported about a resource at the last pull. Microsoft's words and estimates, stored as given. */
export interface AzureAdvice {
  at: string | null;
  category?: string;
  impact?: string;
  risk?: string;
  problem?: string;
  solution?: string;
  recommendation?: string;
  current_sku?: string;
  target_sku?: string;
  lookback_days?: string;
  /** Strings as Advisor sends them; never added up. */
  savings?: string;
  annual_savings?: string;
  currency?: string;
}

export interface AzurePolicyState {
  at: string | null;
  state?: string;
  assignment?: string;
  definition?: string;
  action?: string;
  category?: string;
  initiative?: string;
  reference?: string;
}

export interface AzureHealth {
  at: string | null;
  /** Absent on an annotation, which says why the state changed. */
  state?: string;
  previous_state?: string;
  annotation?: string;
  reason?: string;
  category?: string;
  context?: string;
  summary?: string;
}

/** One changed path; `old` and `new` are present only for paths the plugin stores, otherwise `hidden`. */
export interface AzureChangedPath {
  path: string;
  kind: string;
  old?: string | boolean | number | null;
  new?: string | boolean | number | null;
  hidden?: boolean;
}

export interface AzureChangeRecord {
  at: string | null;
  change_type?: string;
  changed_by?: string;
  changed_by_type?: string;
  client?: string;
  operation?: string;
  count?: number;
  changes: AzureChangedPath[];
}

export interface ResourceSignals {
  /** When these were last read; null when they never were (a pull from before they existed). */
  read_at: string | null;
  advisor: AzureAdvice[];
  policy: AzurePolicyState[];
  health: AzureHealth[];
  change: AzureChangeRecord[];
}

export interface ResourcePage {
  ok: boolean;
  resource: CloudResource;
  links_out: CloudLink[];
  links_in: CloudLink[];
  children: CloudResource[];
  changes: CloudChange[];
  exposure: string[];
  signals?: ResourceSignals;
  portal_url: string;
  error?: string;
}

export const getAzureStatus = () => call<AzureStatus>("azure_status");
export const getAzureTree = () => call<AzureTree>("azure_tree");
export const getAzureResource = (id: string) => call<ResourcePage>("azure_resource", { id });
export const listAzureRuns = async () => must(await call<{ ok: boolean; runs: AzureRun[]; error?: string }>("azure_history"), "Could not load the pulls.");

/** One waste rule that fired on one resource: a pointer to look, not a verdict. `usd_month` is a list price when the price list could give one. */
export interface WasteFinding {
  rule: string;
  title: string;
  id: string;
  native_id: string;
  name: string;
  label: string;
  subscription: string;
  resource_group: string;
  region: string;
  evidence: string;
  as_of: string | null;
  usd_month?: number;
  /** What it cost last month, when a cost answer for it is kept (actual cost, credits not deducted). */
  cost_last_month?: { cost: number; currency: string; period: string; fetched_at: string };
}

export interface WasteList {
  ok: boolean;
  as_of: string | null;
  findings: WasteFinding[];
  total_usd_month: number;
  priced: number;
  unpriced: number;
  priced_at: string | null;
  error?: string;
}

/** One Cost Management answer (actual cost, before tax), as `azure_cost` gives it. Figures are quoted as returned, never added up here. */
export interface CostAnswer {
  ok: boolean;
  question: "spend" | "forecast" | "budgets" | "item";
  headline: string;
  period: { from: string; to: string; timezone: string };
  totals: { currency: string; cost: number }[];
  rows: { name: string; cost: number; currency: string }[];
  rows_not_shown: number;
  scope: { complete: boolean; covers: string; not_included: { subscription: string; reason: string }[] };
  fetched_at: string;
  agreement: string;
  /** The wording Microsoft's cost data needs beside every figure: lateness, credits, partner rates; the last line is for the model. */
  notes: string[];
  error?: string;
}

export const getAzureCost = (params: { question?: CostAnswer["question"]; timeframe?: string; group_by?: string; scope?: string; resource_group?: string }) =>
  call<CostAnswer>("azure_cost", params);

export const getAzureWaste = (params: Pick<ResourceFilters, "subscription" | "resource_group"> = {}) => call<WasteList>("azure_waste", params);

export const listAzureResources = (params: ResourceFilters) => call<ResourceList>("azure_resources", params);

export const listAzureRunChanges = async (runId: string, page: { limit?: number; offset?: number } = {}) =>
  must(
    await call<{ ok: boolean; changes: CloudChange[]; total: number; error?: string }>("azure_history", { run_id: runId, ...page }),
    "Could not load the changes.",
  );

/** `{ok: false, reason}` is a normal answer (busy, too soon, not configured, disabled), not an exception. */
export const startAzurePull = () =>
  call<{ ok: boolean; run_id?: string; error?: string; reason?: "busy" | "too_soon" | "not_configured" | "disabled" }>("azure_pull");

interface ConfigAnswer {
  ok: boolean;
  enabled: boolean;
  config: AzureConfig;
  credential: AzureCredential;
  logs_credential?: AzureLogsCredential;
  error?: string;
}

export const getAzureConfig = async () => must(await call<ConfigAnswer>("azure_config", { action: "get" }), "Could not load the Azure settings.");

/** `secret` blank or absent keeps the stored one. */
export const saveAzureConfig = async (input: { config: Partial<AzureConfig>; secret?: string; secret_expires_on?: string }) =>
  must(await call<ConfigAnswer>("azure_config", { action: "save", ...input }), "Could not save the Azure settings.");

export const generateAzureCertificate = async () =>
  must(
    await call<{ ok: boolean; credential: AzureCredential; public_certificate: string; error?: string }>("azure_config", {
      action: "generate_certificate",
    }),
    "Could not create the certificate.",
  );

export const getAzurePublicCertificate = async () =>
  must(await call<{ ok: boolean; public_certificate: string; error?: string }>("azure_config", { action: "public_certificate" }), "No certificate is set.");

/** The log application's own certificate (key D): made here, only its public half comes back. */
export const generateAzureLogsCertificate = async () =>
  must(
    await call<{ ok: boolean; logs_credential: AzureLogsCredential; public_certificate: string; error?: string }>("azure_config", {
      action: "generate_logs_certificate",
    }),
    "Could not create the log certificate.",
  );

export const getAzureLogsPublicCertificate = async () =>
  must(
    await call<{ ok: boolean; public_certificate: string; error?: string }>("azure_config", { action: "public_logs_certificate" }),
    "No log certificate is set.",
  );

export const clearAzureCredential = async () =>
  must(await call<{ ok: boolean; error?: string }>("azure_config", { action: "clear_credential" }), "Could not clear the credential.");

export const testAzureConnection = () =>
  call<{
    ok: boolean;
    signed_in: boolean;
    reason?: string;
    message: string;
    subscriptions: { id: string; name: string; state: string }[];
    resources: number;
    error?: string;
  }>("azure_test");
