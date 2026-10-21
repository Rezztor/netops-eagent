/** Bulk edit: one pending `update` per device, grouped by a batch id. */

import { apiRequest } from "./client";

export interface BulkProposeResult {
  ok: boolean;
  batch_id?: string;
  created?: number;
  unchanged?: number;
  failed?: { device_id: string; error: string }[];
  error?: string;
}

export async function proposeMany(
  deviceIds: string[],
  fields: Record<string, unknown>,
  note?: string,
) {
  return apiRequest<BulkProposeResult>("plugins/_ea_inventory/inventory_propose_many", {
    body: { device_ids: deviceIds, fields, note },
  });
}
