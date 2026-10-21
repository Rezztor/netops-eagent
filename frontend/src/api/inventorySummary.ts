/** One request for the Inventory tab badges and the four attention tiles. */

import { apiRequest } from "./client";

export interface InventorySummary {
  ok: boolean;
  devices: number;
  sites: number;
  pending: number;
  conflicts: number;
  lifecycle: { expired: number; soon: number };
  incomplete: number;
  /** Mean record completeness, 0–100. */
  average: number;
  stale: { count: number; names: string[] };
  error?: string;
}

export async function getInventorySummary() {
  return apiRequest<InventorySummary>("plugins/_ea_inventory/inventory_summary", { body: {} });
}
