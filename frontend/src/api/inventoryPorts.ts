/** Observed ports and parts of a device: a dated snapshot, not a proposal. */

import { apiRequest } from "./client";

export interface PortNeighbor {
  name: string;
  port: string;
  /** Set when the neighbour's name matches an inventory device. */
  device_id: string | null;
}

export interface PortRow {
  name: string;
  alias: string;
  description: string;
  admin_up: boolean | null;
  oper_up: boolean | null;
  speed: string;
  media: string;
  present: boolean | null;
  poe_watts: number | null;
  mode: string;
  vlans: number[];
  ip_prefixes: string[];
  mac: string;
  neighbor: PortNeighbor | null;
  is_uplink: boolean;
  uplink_source: "marked" | "neighbour" | "";
}

export interface ComponentRow {
  kind: "chassis" | "module" | "psu" | "fan" | "transceiver" | "stack_member" | "licence";
  name: string;
  part_number: string;
  serial: string;
  description: string;
  expires_at: string | null;
}

export interface PortsSummary {
  total: number;
  up: number;
  down: number;
  unknown: number;
  poe: { ports: number; watts: number } | null;
  optics: { media: string; count: number }[];
  vlans: number[];
  mgmt_subnet: string;
  uplinks: { port: string; neighbor: string; neighbor_port: string; device_id: string | null }[];
}

export interface PortsResponse {
  ok: boolean;
  /** null when the device has never been read. */
  observed_at?: string | null;
  interfaces?: PortRow[];
  components?: ComponentRow[];
  summary?: PortsSummary;
  error?: string;
}

export const getPorts = (deviceId: string) =>
  apiRequest<PortsResponse>("plugins/_ea_inventory/inventory_ports", { body: { device_id: deviceId } });

export const refreshPorts = (input: { devices?: string[]; dry_run?: boolean } = {}) =>
  apiRequest<{
    ok: boolean;
    refreshed?: number;
    failed?: { device: string; error: string }[];
    skipped?: number;
    interfaces?: number;
    components?: number;
    error?: string;
  }>("plugins/_ea_inventory/inventory_refresh_ports", { body: input });

export const markUplink = (input: { device_id: string; name: string; is_uplink: boolean }) =>
  apiRequest<{ ok: boolean; error?: string }>("plugins/_ea_inventory/inventory_port_uplink", { body: input });

export const getVlan = (vlan: number) =>
  apiRequest<{
    ok: boolean;
    vlan?: number;
    ports?: { device_id: string; device: string; port: string; mode: string }[];
    error?: string;
  }>("plugins/_ea_inventory/inventory_vlan", { body: { vlan } });

export interface ScheduleStatus {
  ok: boolean;
  /** 0 = scheduled refresh is off. */
  interval_hours?: number;
  last_run?: { started_at: string; finished_at: string | null; status: string; counts: Record<string, number> } | null;
  next_run_at?: string | null;
  error?: string;
}

export const getSchedule = () => apiRequest<ScheduleStatus>("plugins/_ea_inventory/inventory_schedule", { body: {} });
