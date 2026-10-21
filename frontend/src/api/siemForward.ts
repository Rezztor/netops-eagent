/**
 * Where a copy of this instance's audit trail is sent.
 *
 * `plugin_enabled` is the plugin's own toggle from the Plugins screen, not a
 * permission and not the `enabled` field inside the config: the card uses it to
 * take itself off the Settings screen when the plugin is switched off.
 */

import { apiRequest } from "./client";

export interface SiemForwardConfig {
  enabled: boolean;
  host: string;
  port: number;
  syslog_format: string;
  facility: number;
  device_vendor: string;
  device_product: string;
  min_severity: number;
  queue_size: number;
}

export interface SiemForwardStats {
  sent: number;
  dropped_queue_full: number;
  send_errors: number;
}

export interface SiemForwardState {
  ok: boolean;
  plugin_enabled: boolean;
  config: SiemForwardConfig;
  device_version: string;
  syslog_formats: string[];
  stats: SiemForwardStats;
  error?: string;
}

export async function getSiemForwardConfig(): Promise<SiemForwardState> {
  const result = await apiRequest<SiemForwardState>(
    "plugins/_ea_siem_forward/siem_forward_config",
    { body: { action: "get" } },
  );
  if (!result.ok) throw new Error(result.error || "Could not read the SIEM forwarding settings.");
  return result;
}

export async function saveSiemForwardConfig(config: Partial<SiemForwardConfig>) {
  const result = await apiRequest<{ ok: boolean; config?: SiemForwardConfig; stats?: SiemForwardStats; error?: string }>(
    "plugins/_ea_siem_forward/siem_forward_config",
    { body: { action: "save", config } },
  );
  if (!result.ok) throw new Error(result.error || "Could not save the SIEM forwarding settings.");
  return result;
}

export async function resetSiemForwardStats() {
  return apiRequest<{ ok: boolean; stats?: SiemForwardStats; error?: string }>(
    "plugins/_ea_siem_forward/siem_forward_config",
    { body: { action: "reset_stats" } },
  );
}

/**
 * Sends one synthetic event and reports what happened. A success means the
 * packet left this host — over UDP nothing can confirm more than that, and the
 * card says so rather than implying delivery.
 */
export async function sendSiemForwardTest() {
  return apiRequest<{ ok: boolean; line?: string; error?: string }>(
    "plugins/_ea_siem_forward/siem_forward_test",
    { body: {} },
  );
}
