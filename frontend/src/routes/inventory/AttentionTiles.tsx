/** The four "what needs attention" tiles on top of the Devices tab. */

import type { InventorySummary } from "@/api/inventorySummary";

import styles from "./AttentionTiles.module.css";
import { StatTile } from "./kit";

export type TileTarget = "review" | "health" | "sources";

export function AttentionTiles({
  summary,
  onGo,
}: {
  summary: InventorySummary;
  onGo: (tab: TileTarget) => void;
}) {
  const waiting = summary.pending + summary.conflicts;
  const alerts = summary.lifecycle.expired + summary.lifecycle.soon;
  const { count, names } = summary.stale;
  return (
    <div className={styles.tiles}>
      <StatTile
        label="Waiting for your review"
        value={waiting}
        tone={waiting ? "warn" : "none"}
        sub={`${summary.pending} pending changes · ${summary.conflicts} conflicts`}
        onClick={() => onGo("review")}
      />
      <StatTile
        label="Lifecycle alerts"
        value={alerts}
        tone={summary.lifecycle.expired ? "crit" : alerts ? "warn" : "none"}
        sub={`${summary.lifecycle.expired} expired · ${summary.lifecycle.soon} ending soon`}
        onClick={() => onGo("health")}
      />
      <StatTile
        label="Incomplete records"
        value={summary.incomplete}
        sub={`Records are ${Math.round(summary.average)}% complete on average`}
        onClick={() => onGo("health")}
      />
      <StatTile
        label="Not seen for 7 days"
        value={count}
        sub={names.length ? names.join(", ") : "Every device has been seen this week"}
        onClick={() => onGo("sources")}
      />
    </div>
  );
}
