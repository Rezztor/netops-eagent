/**
 * Inventory report endpoints — lifecycle, data quality, racks.
 * Shapes match docs/plans/2026-10-02-inventory-asset-record-build.md.
 */

import { apiRequest } from "./client";

export type LifecycleKind = "warranty" | "support" | "eol";
export type LifecycleState = "none" | "ok" | "soon" | "expired";

export interface LifecycleStateCounts {
  expired: number;
  soon: number;
  ok: number;
  missing: number;
}

export interface LifecycleItem {
  device_id: string;
  name: string;
  site: string;
  vendor: string;
  model: string;
  kind: LifecycleKind;
  date: string | null;
  days: number | null;
  state: LifecycleState;
}

export interface LifecycleResponse {
  ok: boolean;
  today?: string;
  within_days?: number;
  /** Devices with none of the three lifecycle dates. */
  no_dates?: number;
  counts?: {
    warranty: LifecycleStateCounts;
    support: LifecycleStateCounts;
    eol: LifecycleStateCounts;
  };
  items?: LifecycleItem[];
  error?: string;
}

export interface QualityByField {
  field: string;
  missing: number;
}

export interface QualityDevice {
  device_id: string;
  name: string;
  site: string;
  percent: number;
  missing: string[];
}

export interface QualityResponse {
  ok: boolean;
  required?: string[];
  total?: number;
  complete?: number;
  average?: number;
  by_field?: QualityByField[];
  devices?: QualityDevice[];
  error?: string;
}

export interface RackDevice {
  device_id: string;
  name: string;
  device_type: string;
  model: string;
  status: string;
  rack_unit: number | null;
  u_height: number | null;
}

/** One rack, empty ones included; the names are derived from the rack's place. */
export interface RackGroup {
  place_id: string;
  site: string;
  location: string;
  building: string;
  floor: string;
  room: string;
  rack: string;
  used_u: number;
  devices: RackDevice[];
}

export interface RacksResponse {
  ok: boolean;
  racks?: RackGroup[];
  unplaced?: number;
  error?: string;
}

export async function getLifecycle(
  params: {
    within_days?: number;
    kind?: LifecycleKind;
  } = {},
) {
  return apiRequest<LifecycleResponse>("plugins/_ea_inventory/inventory_lifecycle", {
    body: params,
  });
}

export async function getQuality() {
  return apiRequest<QualityResponse>("plugins/_ea_inventory/inventory_quality", {
    body: {},
  });
}

export async function getRacks(params: { place?: string } = {}) {
  return apiRequest<RacksResponse>("plugins/_ea_inventory/inventory_racks", {
    body: params,
  });
}
