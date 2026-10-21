/**
 * Shared pieces of the redesigned Inventory screens (docs/plans/2026-10-02-inventory-redesign).
 * Colours come from the theme tokens, so both themes work. Anything the app's own
 * components already do (Button, Tag, Card, Table, Modal, Drawer) stays there.
 */

import type { ReactNode } from "react";

import type { DeviceComputed, FieldSource, LifecycleDate } from "@/api/inventory";

import styles from "./kit.module.css";

export type Tone = "ok" | "warn" | "crit" | "accent" | "none";

const cx = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(" ");

/** A thin bar with its percentage, for record completeness. */
export function Meter({ percent, tone = "accent" }: { percent: number; tone?: Tone }) {
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  return (
    <span className={styles.meter}>
      <span className={styles.meterTrack} role="img" aria-label={`${value}% complete`}>
        <span className={cx(styles.meterFill, styles[`fill-${tone}`])} style={{ width: `${value}%` }} />
      </span>
      <span className={styles.meterText}>{value}%</span>
    </span>
  );
}

/** A small set of mutually exclusive choices (column sets, time windows, queues). */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { id: T; label: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className={styles.segmented}>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          aria-pressed={option.id === value}
          className={cx(styles.segment, option.id === value && styles.segmentOn)}
          onClick={() => onChange(option.id)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** A number with its label and one line of context. A button when it leads somewhere. */
export function StatTile({
  label,
  value,
  sub,
  tone = "none",
  onClick,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className={styles.tileLabel}>{label}</span>
      <span className={cx(styles.tileValue, styles[`text-${tone}`])}>{value}</span>
      {sub && <span className={styles.tileSub}>{sub}</span>}
    </>
  );
  return onClick ? (
    <button type="button" className={cx(styles.tile, styles.tileButton)} onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className={styles.tile}>{body}</div>
  );
}

/** A titled group on a record or form page. */
export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className={styles.section}>
      <header className={styles.sectionHead}>
        <h3 className={styles.sectionTitle}>{title}</h3>
        {action}
      </header>
      {children}
    </section>
  );
}

export interface KVRow {
  label: string;
  value: ReactNode;
  /** "" when a person set it; otherwise where it came from (see `sourceLabel`). */
  source?: string;
}

/** Label / value rows. An empty value reads "not set"; a source shows only when it is not a person. */
export function KV({ rows }: { rows: KVRow[] }) {
  return (
    <dl className={styles.kv}>
      {rows.map((row) => (
        <div key={row.label} className={styles.kvRow}>
          <dt className={styles.kvLabel}>{row.label}</dt>
          <dd className={styles.kvValue}>
            {row.value === "" || row.value == null ? <span className={styles.unset}>not set</span> : row.value}
          </dd>
          {row.source && <span className={styles.kvSource}>{row.source}</span>}
        </div>
      ))}
    </dl>
  );
}

/** A quiet explanatory strip (what approving means, what a source never does). */
export function Notice({ tone = "none", children }: { tone?: Tone; children: ReactNode }) {
  return <div className={cx(styles.notice, styles[`notice-${tone}`])}>{children}</div>;
}

/** "" for a value a person stands behind; the feed's name for one a machine set. */
export function sourceLabel(source?: FieldSource | null): string {
  const name = source?.source ?? "";
  if (name === "facts") return "device";
  if (name === "solarwinds") return "solarwinds";
  return "";
}

const FIELD_LABELS: Record<string, string> = {
  place_id: "Place",
  site_id: "Site",
  mgmt_ip: "Management IP",
  device_type: "Type",
  base_mac: "Base MAC",
  ha_group: "HA group",
  u_height: "Height in U",
  eol_date: "End of life",
  warranty_expiry: "Warranty ends",
  support_expiry: "Support ends",
  purchase_date: "Purchased",
};

/** A field name as a person would say it ("mgmt_ip" is "Management IP"). */
export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.charAt(0).toUpperCase() + field.slice(1).replace(/_/g, " ");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function monthYear(iso: string): string {
  const [year, month] = iso.split("-");
  return `${MONTHS[Number(month) - 1] ?? ""} ${year}`.trim();
}

const days = (n: number) => `${n} day${n === 1 ? "" : "s"}`;

/** One line for the list and the record header: the worst lifecycle fact, in words. */
export function lifecycleSummary(computed?: DeviceComputed | null): { text: string; tone: Tone } {
  const life = computed?.lifecycle;
  if (!life) return { text: "No dates", tone: "none" };
  const kinds: [string, LifecycleDate][] = [
    ["Warranty", life.warranty],
    ["Support", life.support],
    ["End of life", life.eol],
  ];
  const worst = (state: string) =>
    kinds
      .filter(([, d]) => d.state === state && d.days != null)
      .sort((a, b) => (a[1].days ?? 0) - (b[1].days ?? 0))[0];

  const expired = worst("expired");
  if (expired) {
    const [kind, d] = expired;
    const ago = days(Math.abs(d.days ?? 0));
    return { text: kind === "End of life" ? `End of life passed ${ago} ago` : `${kind} expired ${ago} ago`, tone: "crit" };
  }
  const soon = worst("soon");
  if (soon) {
    const [kind, d] = soon;
    const left = days(d.days ?? 0);
    return { text: kind === "End of life" ? `End of life in ${left}` : `${kind} ends in ${left}`, tone: "warn" };
  }
  const ok = worst("ok");
  if (ok?.[1].date) {
    const [kind, d] = ok;
    return { text: kind === "End of life" ? `End of life ${monthYear(d.date!)}` : `Covered to ${monthYear(d.date!)}`, tone: "none" };
  }
  return { text: "No dates", tone: "none" };
}
