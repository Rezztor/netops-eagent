/** Health tab: lifecycle window, data quality, not-seen-recently. */

import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

import { getBackupReport, runBackup, type BackupReportItem } from "@/api/inventoryBackups";
import {
  getLifecycle,
  getQuality,
  type LifecycleItem,
  type LifecycleKind,
} from "@/api/inventoryReports";
import {
  listStale,
  proposeDecommissionStale,
  type StaleDevice,
} from "@/api/inventory";
import {
  Button,
  CenteredMessage,
  EmptyRow,
  Mono,
  Table,
  Tag,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useUrlEnum, useUrlState } from "@/lib/useUrlState";

import { Segmented } from "./kit";
import styles from "./HealthPanel.module.css";

const WINDOWS = ["30", "90", "180"] as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const FIELD_LABELS: Record<string, string> = {
  name: "Name",
  device_type: "Type",
  role: "Role",
  vendor: "Vendor",
  model: "Model",
  serial: "Serial",
  mgmt_ip: "Management IP",
  place_id: "Place (to room level)",
  status: "Status",
};

const KIND_LABEL: Record<LifecycleKind, string> = {
  warranty: "Warranty",
  support: "Support",
  eol: "EOL",
};

function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return `${Number(d)} ${MONTHS[Number(m) - 1] ?? ""} ${y}`.trim();
}

function timeLeft(item: LifecycleItem): { text: string; tone: "crit" | "warn" } {
  const n = Math.abs(item.days ?? 0);
  if (item.state === "expired") {
    return { text: `expired ${n} day${n === 1 ? "" : "s"} ago`, tone: "crit" };
  }
  return { text: `${n} day${n === 1 ? "" : "s"}`, tone: "warn" };
}

function monthStrip(today: string, items: LifecycleItem[]) {
  const base = new Date(`${today}T12:00:00`);
  const months = Array.from({ length: 11 }, (_, i) => {
    const d = new Date(base.getFullYear(), base.getMonth() + i, 1);
    return { key: `${d.getFullYear()}-${d.getMonth()}`, label: MONTHS[d.getMonth()]!, bars: [] as ("warn" | "crit")[] };
  });
  let past = 0;
  for (const item of items) {
    if (item.state === "expired") {
      past += 1;
      continue;
    }
    if (!item.date) continue;
    const [y, m] = item.date.split("-").map(Number);
    const bucket = months.find((col) => col.key === `${y}-${(m ?? 1) - 1}`);
    if (bucket) bucket.bars.push("warn");
  }
  return { past, months };
}

export function HealthPanel({
  canManage,
  onOpenDevice,
}: {
  canManage: boolean;
  onOpenDevice: (id: string) => void;
}) {
  const toast = useToast();
  const [, setParams] = useSearchParams();
  const [windowDays, setWindowDays] = useUrlEnum("window", WINDOWS, "90");
  const [kindRaw, setKind] = useUrlState("kind", "");
  const kind = (["warranty", "support", "eol"].includes(kindRaw) ? kindRaw : "") as "" | LifecycleKind;

  const loadLife = useCallback(
    () =>
      getLifecycle({
        within_days: Number(windowDays),
        ...(kind ? { kind } : {}),
      }),
    [windowDays, kind],
  );
  const loadQuality = useCallback(() => getQuality(), []);
  const loadStale = useCallback(() => listStale(), []);

  const life = useAsync(loadLife, [windowDays, kind]);
  const quality = useAsync(loadQuality, []);
  const stale = useAsync(loadStale, []);
  const loadBackups = useCallback(() => getBackupReport(), []);
  const backups = useAsync(loadBackups, []);
  const backupData = backups.state.status === "ready" ? backups.state.data : null;
  const backupItems: BackupReportItem[] = backupData?.ok ? (backupData.items ?? []) : [];

  const lifeData = life.state.status === "ready" ? life.state.data : null;
  const lifeReady = Boolean(lifeData?.ok);
  const items: LifecycleItem[] = lifeData?.ok ? (lifeData.items ?? []) : [];
  const today = lifeData?.ok ? (lifeData.today ?? new Date().toISOString().slice(0, 10)) : "";
  const strip = today ? monthStrip(today, items) : { past: 0, months: [] };
  const noDates = lifeData?.ok ? (lifeData.no_dates ?? 0) : 0;

  const qualityData = quality.state.status === "ready" ? quality.state.data : null;
  const qReady = Boolean(qualityData?.ok);
  const total = qualityData?.ok ? (qualityData.total ?? 0) : 0;
  const complete = qualityData?.ok ? (qualityData.complete ?? 0) : 0;
  const average = qualityData?.ok ? (qualityData.average ?? 0) : 0;
  const byField = qualityData?.ok ? (qualityData.by_field ?? []) : [];
  const incomplete = qualityData?.ok ? (qualityData.devices ?? []) : [];
  const topMissing = [...byField].filter((f) => f.missing > 0).sort((a, b) => b.missing - a.missing)[0];

  const staleData = stale.state.status === "ready" ? stale.state.data : null;
  const staleReady = Boolean(staleData?.ok);
  const staleDevices: StaleDevice[] = staleData?.ok ? (staleData.devices ?? []) : [];
  const staleDays = staleData?.ok ? staleData.stale_days : undefined;

  function openDevicesFiltered() {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", "devices");
        return next;
      },
      { replace: true },
    );
  }

  async function proposeOne(id: string) {
    try {
      const result = await proposeDecommissionStale({ device_ids: [id] });
      if (!result.ok) throw new Error(result.error || "Propose failed.");
      toast.success(`Proposed ${result.created ?? 0} decommission(s).`);
      stale.reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function backUpOne(id: string) {
    try {
      const result = await runBackup([id]);
      const mine = result.results?.[0];
      if (!result.ok || !mine?.ok) throw new Error(mine?.error || result.error || "Backup failed.");
      toast.success("Backed up.");
      backups.reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  return (
    <div className={styles.root} data-testid="health-panel">
      <div className={styles.grid}>
        <section data-testid="lifecycle-panel" style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius)", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <h3 style={{ margin: 0, fontSize: 12, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--ink-2)" }}>
              Lifecycle
            </h3>
            <div data-testid="lifecycle-window">
              <Segmented
                label="Window"
                options={WINDOWS.map((d) => ({ id: d, label: `${d} days` }))}
                value={windowDays}
                onChange={setWindowDays}
              />
            </div>
          </div>
          <div className={styles.kindBar} data-testid="lifecycle-kind">
            <Segmented
              label="Kind"
              options={[
                { id: "all", label: "All" },
                { id: "warranty", label: "Warranty" },
                { id: "support", label: "Support" },
                { id: "eol", label: "EOL" },
              ]}
              value={kind || "all"}
              onChange={(id) => setKind(id === "all" ? "" : id)}
            />
          </div>
          <span className={styles.hint}>
            Warranty, support and end-of-life dates that have passed or end within the window. One block is one device.
          </span>

          {life.state.status === "loading" && <CenteredMessage title="Loading lifecycle…" />}
          {life.state.status === "error" && (
            <CenteredMessage title="Could not load lifecycle" detail={life.state.error.message} action={<Button variant="primary" onClick={life.reload}>Retry</Button>} />
          )}
          {life.state.status === "ready" && !life.state.data.ok && (
            <CenteredMessage title="Could not load lifecycle" detail={life.state.data.error} action={<Button variant="primary" onClick={life.reload}>Retry</Button>} />
          )}
          {lifeReady && (
            <>
              <div className={styles.strip} data-testid="lifecycle-strip">
                <div className={styles.stripCol}>
                  <div className={styles.stripBars}>
                    {strip.past === 0 ? <span className={`${styles.bar} ${styles.barEmpty}`} /> : Array.from({ length: Math.min(strip.past, 6) }, (_, i) => (
                      <span key={i} className={`${styles.bar} ${styles.barCrit}`} />
                    ))}
                  </div>
                  <span className={styles.stripLabel}>Past</span>
                </div>
                {strip.months.map((col) => (
                  <div key={col.key} className={styles.stripCol}>
                    <div className={styles.stripBars}>
                      {col.bars.length === 0 ? (
                        <span className={`${styles.bar} ${styles.barEmpty}`} />
                      ) : (
                        col.bars.slice(0, 6).map((tone, i) => (
                          <span key={i} className={`${styles.bar} ${tone === "crit" ? styles.barCrit : styles.barWarn}`} />
                        ))
                      )}
                    </div>
                    <span className={styles.stripLabel}>{col.label}</span>
                  </div>
                ))}
              </div>

              <div className={styles.scroll}>
                <Table>
                  <thead>
                    <tr>
                      <th>Device</th>
                      <th>Model</th>
                      <th>What</th>
                      <th>Date</th>
                      <th>Time left</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.length === 0 ? (
                      <EmptyRow colSpan={5}>No devices are expired or ending soon in this window.</EmptyRow>
                    ) : (
                      items.map((item) => {
                        const left = timeLeft(item);
                        return (
                          <tr
                            key={`${item.device_id}:${item.kind}`}
                            data-testid={`lifecycle-row-${item.device_id}-${item.kind}`}
                            data-expired={item.state === "expired" ? "true" : undefined}
                          >
                            <td>
                              <button
                                type="button"
                                className={styles.link}
                                data-testid={`lifecycle-open-${item.device_id}`}
                                onClick={() => onOpenDevice(item.device_id)}
                              >
                                <Mono>{item.name}</Mono>
                              </button>
                            </td>
                            <td style={{ color: "var(--ink-2)" }}>{item.model || "—"}</td>
                            <td>{KIND_LABEL[item.kind]}</td>
                            <td style={{ fontVariantNumeric: "tabular-nums" }}>{formatDate(item.date)}</td>
                            <td>
                              <Tag tone={left.tone}>{left.text}</Tag>
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </Table>
              </div>
              {noDates > 0 && (
                <span className={styles.foot}>{noDates === 1 ? "1 device has" : `${noDates} devices have`} no lifecycle dates yet.</span>
              )}
            </>
          )}
        </section>

        <section data-testid="quality-panel" style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius)", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <h3 style={{ margin: 0, fontSize: 12, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--ink-2)" }}>
              Data quality
            </h3>
            {topMissing && (
              <Button data-testid="quality-fix-top" onClick={openDevicesFiltered}>
                {`Fix ${fieldLabel(topMissing.field).toLowerCase()} on ${topMissing.missing} device${topMissing.missing === 1 ? "" : "s"}`}
              </Button>
            )}
          </div>

          {quality.state.status === "loading" && <CenteredMessage title="Loading data quality…" />}
          {quality.state.status === "error" && (
            <CenteredMessage title="Could not load data quality" detail={quality.state.error.message} action={<Button variant="primary" onClick={quality.reload}>Retry</Button>} />
          )}
          {quality.state.status === "ready" && !quality.state.data.ok && (
            <CenteredMessage title="Could not load data quality" detail={quality.state.data.error} action={<Button variant="primary" onClick={quality.reload}>Retry</Button>} />
          )}
          {qReady && (
            <>
              <div className={styles.avg}>
                <span className={styles.avgValue}>{average}%</span>
                <span className={styles.avgSub}>
                  average completeness · {complete} of {total} records are complete
                </span>
              </div>
              <div className={styles.fieldRows}>
                {byField
                  .filter((f) => f.missing > 0)
                  .map((row) => (
                    <div key={row.field} className={styles.fieldRow} data-testid={`quality-field-${row.field}`}>
                      <span className={styles.fieldName}>{fieldLabel(row.field)}</span>
                      <span className={styles.fieldTrack}>
                        <span
                          className={styles.fieldFill}
                          style={{ width: `${total ? Math.min(100, (row.missing / total) * 100) : 0}%` }}
                        />
                      </span>
                      <span className={styles.fieldCount}>
                        {row.missing} of {total}
                      </span>
                    </div>
                  ))}
              </div>
              <div className={styles.deviceList}>
                {incomplete.length === 0 ? (
                  <span className={styles.foot}>Every device has the required fields.</span>
                ) : (
                  incomplete.map((device) => (
                    <div key={device.device_id} className={styles.deviceRow} data-testid={`quality-row-${device.device_id}`}>
                      <div className={styles.deviceLeft}>
                        <button
                          type="button"
                          className={styles.link}
                          data-testid={`quality-open-${device.device_id}`}
                          onClick={() => onOpenDevice(device.device_id)}
                        >
                          <Mono>{device.name}</Mono>
                        </button>
                        <span className={styles.deviceMeter}>
                          <span className={styles.deviceMeterTrack}>
                            <span className={styles.deviceMeterFill} style={{ width: `${device.percent}%` }} />
                          </span>
                          <span className={styles.devicePct}>{device.percent}%</span>
                        </span>
                      </div>
                      <div className={styles.chips}>
                        {device.missing.map((field) => (
                          <Tag key={field} tone="warn">
                            {fieldLabel(field).toLowerCase()}
                          </Tag>
                        ))}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </>
          )}
        </section>
      </div>

      <section data-testid="stale-panel" style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius)", minWidth: 0 }}>
        <h3 style={{ margin: 0, fontSize: 12, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--ink-2)" }}>
          Not seen recently
        </h3>
        {stale.state.status === "loading" && <CenteredMessage title="Loading…" />}
        {stale.state.status === "error" && (
          <CenteredMessage title="Could not load stale devices" detail={stale.state.error.message} />
        )}
        {stale.state.status === "ready" && !stale.state.data.ok && (
          <CenteredMessage title="Stale list refused" detail={stale.state.data.error} />
        )}
        {staleReady && staleDevices.length === 0 && (
          <span className={styles.foot}>
            No active device has gone unseen for {staleDays ?? "the configured"} days.
          </span>
        )}
        {staleReady &&
          staleDevices.map((d) => (
            <div key={d.id} className={styles.staleRow} data-testid={`stale-row-${d.id}`}>
              <div className={styles.staleLeft}>
                <button type="button" className={styles.link} onClick={() => onOpenDevice(d.id)}>
                  <Mono>{d.name}</Mono>
                </button>
                <Tag tone={d.status === "active" ? "muted" : "crit"}>{d.status}</Tag>
                <span className={styles.staleMeta}>
                  {d.days_unseen == null
                    ? "Never seen"
                    : `Last seen ${d.sources[0] ? `by ${d.sources[0]} ` : ""}${d.days_unseen} day${d.days_unseen === 1 ? "" : "s"} ago`}
                </span>
              </div>
              {canManage && (
                <Button data-testid={`stale-propose-${d.id}`} onClick={() => void proposeOne(d.id)}>
                  Propose decommission
                </Button>
              )}
            </div>
          ))}
      </section>

      <section data-testid="backup-panel" style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius)", minWidth: 0 }}>
        <h3 style={{ margin: 0, fontSize: 12, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--ink-2)" }}>
          Backups
        </h3>
        {backups.state.status === "loading" && <CenteredMessage title="Loading…" />}
        {backups.state.status === "error" && (
          <CenteredMessage title="Could not load backups" detail={backups.state.error.message} />
        )}
        {backups.state.status === "ready" && !backups.state.data.ok && (
          <CenteredMessage title="Could not load backups" detail={backups.state.data.error} />
        )}
        {backupData?.ok && backupItems.length === 0 && (
          <span className={styles.foot}>
            Every active device has a configuration backup from the last {backupData.stale_days ?? "configured"} days.
          </span>
        )}
        {backupData?.ok &&
          backupItems.map((b) => (
            <div key={b.device_id} className={styles.staleRow} data-testid={`backup-row-${b.device_id}`}>
              <div className={styles.staleLeft}>
                <button type="button" className={styles.link} onClick={() => onOpenDevice(b.device_id)}>
                  <Mono>{b.name}</Mono>
                </button>
                <Tag tone={b.state === "none" ? "crit" : "warn"}>{b.state === "none" ? "never backed up" : "stale"}</Tag>
                {b.state === "stale" && b.age_days != null && (
                  <span className={styles.staleMeta}>Last backup {b.age_days} day{b.age_days === 1 ? "" : "s"} ago</span>
                )}
              </div>
              {canManage && (
                <Button data-testid={`backup-now-${b.device_id}`} onClick={() => void backUpOne(b.device_id)}>
                  Back up now
                </Button>
              )}
            </div>
          ))}
      </section>
    </div>
  );
}
