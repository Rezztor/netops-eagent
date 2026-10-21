/**
 * Device inventory API — plugins/_ea_inventory handlers.
 *
 * Every call is a POST via apiRequest. Shapes match the Phase 1/2 build contracts.
 */

import { apiRequest } from "./client";

export type DeviceStatus =
  | "active"
  | "planned"
  | "staged"
  | "offline"
  | "spare"
  | "faulty"
  | "decommissioned";

export type DeviceType =
  | "router"
  | "switch"
  | "firewall"
  | "wireless_controller"
  | "access_point"
  | "load_balancer"
  | "other";

export type LifecycleState = "none" | "ok" | "soon" | "expired";

export interface LifecycleDate {
  date: string | null;
  days: number | null;
  state: LifecycleState;
}

/** Derived on read, never stored (reports.computed). Absent on older payloads. */
export interface DeviceComputed {
  access_method: "ssh" | "https_api" | "";
  monitored: boolean;
  completeness: { percent: number; missing: string[] };
  lifecycle: { warranty: LifecycleDate; support: LifecycleDate; eol: LifecycleDate };
  lifecycle_state: LifecycleState;
  /** null when the executor could not be reached. Absent on older payloads. */
  backup?: {
    taken_at: string | null;
    checked_at: string | null;
    age_days: number | null;
    state: "none" | "ok" | "stale";
  } | null;
}

export type DevicePlatform =
  | "ios"
  | "iosxe"
  | "nxos"
  | "eos"
  | "junos"
  | "fortios"
  | "panos"
  | "sonic"
  | "sonic_community"
  | "other";

export type ProposalKind = "create" | "update" | "decommission";
export type ProposalStatus = "pending" | "approved" | "rejected" | "superseded";
export type ProposalSource = "manual" | "solarwinds" | "extraction" | "agent" | "facts";

export interface FieldSource {
  source: string;
  approved_by: string;
  approved_at: string;
  observed_at: string | null;
}

export interface SiteRef {
  id: string;
  name: string;
}

/** A device's place with every level above it, site first (store.attach_place). */
export interface DevicePlace {
  id: string;
  kind: string;
  /** Effective: false when the place or any ancestor is inactive. */
  active: boolean;
  path: { id: string; kind: string; code: string; name: string }[];
}

export interface Device {
  id: string;
  name: string;
  role: string;
  vendor: string;
  platform: string;
  model: string;
  serial: string;
  asset_tag: string;
  status: string;
  place_id?: string | null;
  place?: DevicePlace | null;
  /** Derived, read only. */
  site: SiteRef | null;
  location?: string;
  mgmt_ip: string;
  software_version: string;
  ha_group: string;
  tags: string[];
  /* Asset record fields. Optional so older payloads and fixtures still type-check. */
  device_type?: string;
  part_number?: string;
  base_mac?: string;
  building?: string;
  floor?: string;
  room?: string;
  rack?: string;
  rack_unit?: number | null;
  u_height?: number | null;
  department?: string;
  custodian?: string;
  support_contact?: string;
  support_contract?: string;
  purchase_date?: string | null;
  warranty_expiry?: string | null;
  support_expiry?: string | null;
  eol_date?: string | null;
  computed?: DeviceComputed;
  custom: Record<string, unknown>;
  field_sources: Record<string, FieldSource>;
  /** Latest observation per external source (Phase 3). Absent on older payloads. */
  sources?: DeviceSource[];
  last_seen_at: string | null;
  /** Latest facts observation time (Phase 4). Absent on older payloads. */
  facts_at?: string | null;
  /** Other devices sharing this device's ha_group (Phase 4). */
  ha_members?: HaMember[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface HaMember {
  id: string;
  name: string;
  status: string;
}

/** Observation provenance on a device (Phase 3 SolarWinds feed). */
export interface DeviceSource {
  source: string;
  source_id: string;
  last_seen_at: string | null;
  stale: boolean;
}

export type SourceRunStatus = "running" | "succeeded" | "failed" | "dry_run";

export interface SourceRunCounts {
  fetched: number;
  create: number;
  update: number;
  unchanged: number;
  skipped: number;
  needs_review: number;
  stale: number;
}

export interface SourceLastRun {
  id: string;
  status: SourceRunStatus;
  started_at: string;
  finished_at: string | null;
  counts: SourceRunCounts;
  error: string | null;
  batch_id: string | null;
}

export interface InventorySource {
  source: string;
  enabled: boolean;
  configured: boolean;
  last_run: SourceLastRun | null;
  nodes_seen: number;
  stale_count: number;
}

export interface NeedsReviewItem {
  node_id: string;
  observed_name: string;
  device_id: string | null;
  device_name: string;
  reason: string;
}

export interface SolarwindsSyncResult {
  run_id: string;
  status: SourceRunStatus;
  counts: SourceRunCounts;
  batch_id: string | null;
  needs_review: NeedsReviewItem[];
  missing_columns: string[];
}

export interface ProposalChange {
  old: unknown;
  new: unknown;
}

/** Evidence on extraction-staged proposals (Phase 2). Other sources may omit fields. */
export interface ProposalEvidence {
  file?: string;
  source_id?: string;
  quote?: string;
  location?: string;
  confidence?: number;
  batch_id?: string;
}

export interface Proposal {
  id: string;
  kind: ProposalKind;
  device_id: string | null;
  device_name: string;
  base_version: number | null;
  changes: Record<string, ProposalChange>;
  source: string;
  evidence: ProposalEvidence | null;
  status: ProposalStatus;
  requested_by: string;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
}

export interface BatchCounts {
  pending: number;
  approved: number;
  rejected: number;
  superseded: number;
}

export interface InventoryBatch {
  batch_id: string;
  file: string;
  source: string;
  requested_by: string;
  requested_at: string;
  counts: BatchCounts;
  total: number;
}

export interface Observation {
  id: string;
  device_id: string | null;
  source: string;
  source_id: string;
  observed_at: string;
  payload: Record<string, unknown>;
}

export interface InventoryFacets {
  vendor: string[];
  platform: string[];
  status: string[];
  site: SiteRef[];
  tag: string[];
  device_type?: string[];
  role?: string[];
  department?: string[];
  device_types?: string[];
  role_suggestions?: string[];
  statuses?: string[];
}

export interface SyncDiff {
  added: string[];
  removed: string[];
  changed: string[];
  warnings: string[];
}

export interface SyncResult {
  ok: boolean;
  error?: string;
  changed?: boolean;
}

export interface ListDevicesParams {
  q?: string;
  /** A place ID (devices at or below it) or "none". */
  place?: string;
  vendor?: string;
  platform?: string;
  status?: string;
  tag?: string;
  device_type?: string;
  role?: string;
  department?: string;
  stale?: boolean;
  limit?: number;
  offset?: number;
}

export async function listDevices(params: ListDevicesParams = {}) {
  return apiRequest<{
    ok: boolean;
    devices?: Device[];
    total?: number;
    facets?: InventoryFacets;
    error?: string;
  }>("plugins/_ea_inventory/inventory_list", { body: params });
}

export async function getDevice(id: string) {
  return apiRequest<{
    ok: boolean;
    device?: Device;
    observations?: Observation[];
    proposals?: Proposal[];
    error?: string;
  }>("plugins/_ea_inventory/inventory_device", { body: { id } });
}

export async function proposeChange(input: {
  kind: ProposalKind;
  device_id?: string;
  fields: Record<string, unknown>;
  source?: ProposalSource;
  evidence?: unknown;
}) {
  return apiRequest<{ ok: boolean; proposal?: Proposal; error?: string }>(
    "plugins/_ea_inventory/inventory_propose",
    {
      body: {
        kind: input.kind,
        ...(input.device_id ? { device_id: input.device_id } : {}),
        fields: input.fields,
        source: input.source ?? "manual",
        ...(input.evidence !== undefined ? { evidence: input.evidence } : {}),
      },
    },
  );
}

export async function listProposals(params: {
  status?: ProposalStatus;
  source?: ProposalSource | string;
  batch_id?: string;
  limit?: number;
  offset?: number;
} = {}) {
  return apiRequest<{
    ok: boolean;
    proposals?: Proposal[];
    total?: number;
    pending_count?: number;
    error?: string;
  }>("plugins/_ea_inventory/inventory_proposals", { body: params });
}

export async function decideProposal(input: {
  id: string;
  decision: "approve" | "reject";
  note?: string;
  edits?: Record<string, unknown>;
}) {
  return apiRequest<{
    ok: boolean;
    proposal?: Proposal;
    device?: Device;
    sync?: SyncResult;
    error?: string;
  }>("plugins/_ea_inventory/inventory_decide", { body: input });
}

export async function listBatches(params: {
  status?: ProposalStatus;
  limit?: number;
  offset?: number;
} = {}) {
  return apiRequest<{
    ok: boolean;
    batches?: InventoryBatch[];
    total?: number;
    error?: string;
  }>("plugins/_ea_inventory/inventory_batches", { body: params });
}

export async function decideBatch(input: {
  batch_id: string;
  decision: "approve" | "reject";
  min_confidence?: number;
  note?: string;
}) {
  return apiRequest<{
    ok: boolean;
    decided?: number;
    failed?: { id: string; error: string }[];
    skipped_low_confidence?: number;
    sync?: SyncResult;
    error?: string;
  }>("plugins/_ea_inventory/inventory_decide_batch", { body: input });
}

export async function exportInventory(format: "csv" | "json") {
  return apiRequest<{
    ok: boolean;
    filename?: string;
    content_type?: string;
    content?: string;
    error?: string;
  }>("plugins/_ea_inventory/inventory_export", { body: { format } });
}

export async function downloadTemplate() {
  return apiRequest<{
    ok: boolean;
    filename?: string;
    content_type?: string;
    content?: string;
    error?: string;
  }>("plugins/_ea_inventory/inventory_template", { body: {} });
}

export async function syncInventory(dryRun = false) {
  return apiRequest<{
    ok: boolean;
    diff?: SyncDiff;
    applied?: boolean;
    error?: string;
  }>("plugins/_ea_inventory/inventory_sync", { body: { dry_run: dryRun } });
}

export async function listSources() {
  return apiRequest<{
    ok: boolean;
    sources?: InventorySource[];
    error?: string;
  }>("plugins/_ea_inventory/inventory_sources", { body: {} });
}

export async function syncSolarwinds(dryRun = false) {
  return apiRequest<{
    ok: boolean;
    run_id?: string;
    status?: SourceRunStatus;
    counts?: SourceRunCounts;
    batch_id?: string | null;
    needs_review?: NeedsReviewItem[];
    missing_columns?: string[];
    error?: string;
  }>("plugins/_ea_inventory/inventory_sync_solarwinds", {
    body: { dry_run: dryRun },
  });
}

/* ------------------------ Phase 4: facts, conflicts, stale ------------------------ */

export type ConflictStatus = "open" | "accepted" | "kept" | "pinned" | "superseded";
export type ConflictDecision = "accept" | "keep" | "pin";

export interface InventoryConflict {
  id: string;
  device_id: string;
  device_name: string;
  field: string;
  current_value: unknown;
  current_source: string;
  proposed_value: unknown;
  source: string;
  observed_at: string | null;
  status: ConflictStatus;
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  created_at: string;
}

export interface InventoryPin {
  device_id: string;
  device_name: string;
  field: string;
  source: string;
  pinned_by: string;
  pinned_at: string;
}

export interface StaleDevice {
  id: string;
  name: string;
  status: string;
  last_seen_at: string | null;
  days_unseen: number | null;
  sources: string[];
}

export interface FactsDeviceResult {
  device: string;
  status: "ok" | "error" | "skipped";
  error?: string;
  platform: string;
  fields_seen: string[];
  proposals: number;
  conflicts: number;
  unchanged: number;
  pinned: number;
  warnings: string[];
}

export async function listConflicts(params: {
  status?: ConflictStatus;
  device_id?: string;
  limit?: number;
  offset?: number;
} = {}) {
  return apiRequest<{
    ok: boolean;
    conflicts?: InventoryConflict[];
    total?: number;
    open_count?: number;
    error?: string;
  }>("plugins/_ea_inventory/inventory_conflicts", { body: params });
}

export async function decideConflict(input: {
  id: string;
  decision: ConflictDecision;
  note?: string;
}) {
  return apiRequest<{
    ok: boolean;
    conflict?: InventoryConflict;
    proposal?: Proposal;
    device?: Device;
    error?: string;
  }>("plugins/_ea_inventory/inventory_conflict_decide", { body: input });
}

export async function decideConflicts(input: {
  ids: string[];
  decision: ConflictDecision;
  note?: string;
}) {
  return apiRequest<{
    ok: boolean;
    decided?: number;
    failed?: { id: string; error: string }[];
    error?: string;
  }>("plugins/_ea_inventory/inventory_conflict_decide_many", { body: input });
}

export async function listPins(params: { device_id?: string } = {}) {
  return apiRequest<{ ok: boolean; pins?: InventoryPin[]; error?: string }>(
    "plugins/_ea_inventory/inventory_pins",
    { body: params },
  );
}

export async function unpinField(input: {
  device_id: string;
  field: string;
  source: string;
}) {
  return apiRequest<{ ok: boolean; error?: string }>(
    "plugins/_ea_inventory/inventory_unpin",
    { body: input },
  );
}

export async function listStale(params: { days?: number } = {}) {
  return apiRequest<{
    ok: boolean;
    devices?: StaleDevice[];
    total?: number;
    stale_days?: number;
    error?: string;
  }>("plugins/_ea_inventory/inventory_stale", { body: params });
}

export async function proposeDecommissionStale(input: {
  device_ids: string[];
  note?: string;
}) {
  return apiRequest<{
    ok: boolean;
    batch_id?: string | null;
    created?: number;
    rejected?: { id: string; reason: string }[];
    error?: string;
  }>("plugins/_ea_inventory/inventory_propose_decommission_stale", { body: input });
}

export async function refreshFacts(input: { devices?: string[]; dry_run?: boolean } = {}) {
  return apiRequest<{
    ok: boolean;
    results?: FactsDeviceResult[];
    batch_id?: string | null;
    counts?: Record<string, number>;
    error?: string;
  }>("plugins/_ea_inventory/inventory_refresh_facts", { body: input });
}
