/**
 * The SolarWinds plugin's connection settings.
 *
 * Gated on `plugins.manage`, enforced server-side in `helpers/api_policy.py` --
 * this decides which instance the agent reads live infrastructure data from.
 *
 * The password never comes back from `get` at all, only whether one is set
 * (`passwordSet`). The field the interface shows is therefore always blank on
 * load; submitting it blank leaves whatever is stored untouched, and typing a
 * value is the only way to change it.
 */

import { apiRequest } from "./client";

export interface SolarWindsConfig {
  host: string;
  port: number;
  verify_tls: boolean;
  username: string;
  timeout_seconds: number;
  max_rows: number;
  max_calls_per_minute: number;
  [key: string]: unknown;
}

const CONFIG = "plugins/_ea_solarwinds/solarwinds_config";

export async function getSolarWindsConfig(): Promise<{
  enabled: boolean;
  editable: string[];
  config: SolarWindsConfig;
  passwordSet: boolean;
}> {
  const result = await apiRequest<{
    ok: boolean;
    enabled?: boolean;
    editable?: string[];
    config?: SolarWindsConfig;
    password_set?: boolean;
    error?: string;
  }>(CONFIG, { body: { action: "get" } });
  if (!result.ok || !result.config) {
    throw new Error(result.error || "Could not load the SolarWinds settings.");
  }
  return {
    enabled: result.enabled ?? false,
    editable: result.editable ?? [],
    config: result.config,
    passwordSet: result.password_set ?? false,
  };
}

export async function saveSolarWindsConfig(
  config: Partial<SolarWindsConfig> & { password?: string },
): Promise<{ config: SolarWindsConfig; passwordSet: boolean }> {
  const result = await apiRequest<{
    ok: boolean;
    config?: SolarWindsConfig;
    password_set?: boolean;
    error?: string;
  }>(CONFIG, { body: { action: "save", config } });
  if (!result.ok || !result.config) {
    throw new Error(result.error || "Could not save the SolarWinds settings.");
  }
  return { config: result.config, passwordSet: result.password_set ?? false };
}
