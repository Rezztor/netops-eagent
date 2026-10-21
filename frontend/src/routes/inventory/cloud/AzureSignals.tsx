/**
 * What Azure itself reported about one resource at the last pull: its health, Advisor's recommendations, policy it does
 * not meet, and Azure's own change records (who changed what). Microsoft's words and estimates, shown as stored:
 * nothing is checked, added up or acted on here.
 */

import type { ReactNode } from "react";

import type { AzureAdvice, AzureChangeRecord, AzureHealth, AzurePolicyState, ResourceSignals } from "@/api/azure";
import { DateTime, Tag } from "@/components";

import { Notice, Section } from "../kit";
import { fmt } from "./blocks/GenericProps";
import styles from "./cloud.module.css";

const IMPACT = { high: "crit", medium: "warn", low: "accent" } as const;
const HEALTH = { available: "ok", degraded: "warn", unavailable: "crit" } as const;

const tone = <T extends string>(map: Record<string, T>, word?: string): T | "muted" => map[(word ?? "").toLowerCase()] ?? "muted";

function Health({ item }: { item: AzureHealth }) {
  return (
    <div className={styles.change}>
      <span>
        {item.state ? <Tag tone={tone(HEALTH, item.state)}>{item.state}</Tag> : <Tag>{item.category || "Note"}</Tag>}{" "}
        {item.annotation ?? ""} {item.at && <DateTime value={item.at} />}
      </span>
      {(item.reason || item.summary) && <span className={styles.dim}>{[item.reason, item.summary].filter(Boolean).join(": ")}</span>}
      {item.previous_state && <span className={styles.sub}>Before: {item.previous_state}</span>}
    </div>
  );
}

function Advice({ item }: { item: AzureAdvice }) {
  const saving = item.savings ? `${item.savings} ${item.currency ?? ""}`.trim() : "";
  return (
    <div className={styles.change}>
      <span>
        <Tag tone={tone(IMPACT, item.impact)}>{item.impact ?? "Advice"}</Tag> {item.category}
      </span>
      <span>{item.problem}</span>
      {item.solution && item.solution !== item.problem && <span className={styles.dim}>{item.solution}</span>}
      {(item.current_sku || item.target_sku) && (
        <span className={styles.sub}>{`${item.current_sku ?? "?"} to ${item.target_sku ?? "?"}`}</span>
      )}
      {saving && (
        <span className={styles.sub}>
          {`Microsoft's estimate at retail rates: ${saving}${item.annual_savings ? ` (${item.annual_savings} a year)` : ""}${item.lookback_days ? `, over ${item.lookback_days} days of use` : ""}`}
        </span>
      )}
    </div>
  );
}

function Policy({ item }: { item: AzurePolicyState }) {
  return (
    <div className={styles.change}>
      <span>
        <Tag tone="warn">{item.state ?? "NonCompliant"}</Tag> {item.assignment}
      </span>
      <span className={styles.sub}>{[item.category, item.action && `effect ${item.action}`, item.reference].filter(Boolean).join(" · ")}</span>
    </div>
  );
}

function ChangeRecord({ item }: { item: AzureChangeRecord }) {
  const who = [item.changed_by, item.changed_by_type && `(${item.changed_by_type})`, item.client && `via ${item.client}`].filter(Boolean).join(" ");
  return (
    <div className={styles.change}>
      <span>
        {item.at && <DateTime value={item.at} />} <Tag tone="accent">{item.change_type ?? "Change"}</Tag> {who}
      </span>
      {item.changes.length > 0 && (
        <ul className={styles.fields}>
          {item.changes.map((c) => (
            <li key={c.path}>{c.hidden ? `${c.path}: changed (the value is not stored)` : `${c.path}: ${fmt(c.old)} became ${fmt(c.new)}`}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Group<T>({ title, items, render }: { title: string; items: T[]; render: (item: T) => ReactNode }) {
  if (!items.length) return null;
  return (
    <div className={styles.inner}>
      <h4 className={styles.innerTitle}>{title}</h4>
      {items.map((x, i) => <div key={i}>{render(x)}</div>)}
    </div>
  );
}

export function AzureSignals({ signals }: { signals?: ResourceSignals }) {
  if (!signals) return null;
  const { read_at: readAt, health, advisor, policy, change } = signals;
  const none = !health.length && !advisor.length && !policy.length && !change.length;
  return (
    <Section title="What Azure says">
      {readAt ? (
        <Notice>
          Read from Azure on <DateTime value={readAt} />. Microsoft&apos;s own wording and estimates, shown as stored; a pointer to look, not a verdict.
        </Notice>
      ) : (
        <Notice tone="warn">Azure&apos;s own findings (health, Advisor, policy, change records) have not been read yet. They come with the next pull.</Notice>
      )}
      {readAt && none && <span className={styles.unset}>Azure reported nothing about this resource.</span>}
      <Group title="Health" items={health} render={(x) => <Health item={x} />} />
      <Group title="Advisor" items={advisor} render={(x) => <Advice item={x} />} />
      <Group title="Policy it does not meet" items={policy} render={(x) => <Policy item={x} />} />
      <Group title="Changed in Azure, last 14 days" items={change} render={(x) => <ChangeRecord item={x} />} />
    </Section>
  );
}
