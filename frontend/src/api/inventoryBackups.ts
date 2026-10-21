/**
 * Config backups (build contract B). The application never receives raw backup text:
 * `viewBackup` and `diffBackup` return the executor's redacted view.
 */

import { apiRequest } from "./client";

export interface BackupRow {
  id: string;
  taken_at: string;
  checked_at: string | null;
  sha256: string;
  size: number;
}

export interface BackupRunResult {
  device_id: string;
  name: string;
  ok: boolean;
  id?: string;
  changed?: boolean;
  error?: string;
}

export type BackupState = "none" | "ok" | "stale";

export interface BackupReportItem {
  device_id: string;
  name: string;
  state: "none" | "stale";
  taken_at: string | null;
  age_days: number | null;
}

const base = "plugins/_ea_inventory/";

export const listBackups = (deviceId: string) =>
  apiRequest<{ ok: boolean; backups?: BackupRow[]; error?: string }>(`${base}inventory_backups`, {
    body: { device_id: deviceId },
  });

export const runBackup = (deviceIds: string[]) =>
  apiRequest<{ ok: boolean; results?: BackupRunResult[]; error?: string }>(`${base}inventory_backup_run`, {
    body: { device_ids: deviceIds },
  });

export const viewBackup = (deviceId: string, id: string) =>
  apiRequest<{ ok: boolean; text?: string; error?: string }>(`${base}inventory_backup_view`, {
    body: { device_id: deviceId, id },
  });

export const diffBackup = (deviceId: string, from: string, to: string) =>
  apiRequest<{ ok: boolean; diff?: string; changed_lines?: number; error?: string }>(`${base}inventory_backup_diff`, {
    body: { device_id: deviceId, from, to },
  });

export const getBackupReport = () =>
  apiRequest<{ ok: boolean; stale_days?: number; items?: BackupReportItem[]; error?: string }>(
    `${base}inventory_backup_report`,
    { body: {} },
  );

/** A device whose newest backup differs from the one before (`inventory_drift`). */
export interface DriftItem {
  device_id: string;
  name: string;
  /** The older and newer backup: what the redacted diff is opened with. */
  from: string;
  to: string;
  /** When the older backup was last seen unchanged, and when the newer one was taken. */
  since: string;
  changed_at: string;
  /** An approved change ran on this device in that window. Not a claim that it is the whole difference. */
  explained: boolean;
  changes: { id: string; summary: string; status: string; approved_by: string; applied_at: string }[];
}

export const getDrift = () =>
  apiRequest<{ ok: boolean; changed?: number; unexplained?: number; items?: DriftItem[]; error?: string }>(
    `${base}inventory_drift`,
    { body: {} },
  );
