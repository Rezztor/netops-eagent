/**
 * The Azure tab: the attention tiles, the tree, and beside it a resource group by category, a list, or one
 * resource. Everything that narrows the view lives in the address bar: node, cat, res, q, type, region, state, tag,
 * without (a missing tag), attention, removed, page. The screen only reads; there is no Add, Edit or Review here.
 */

import { useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { getAzureTree, type Attention, type AzureAsOf, type AzureStatus, type ResourceFilters, type TreeNode } from "@/api/azure";
import { Button, CenteredMessage, DateTime } from "@/components";
import { useAsync } from "@/lib/useAsync";

import { StatTile, Notice } from "../kit";
import { AzureCost } from "./AzureCost";
import { AzureGroup } from "./AzureGroup";
import { AzureResourceList, type ListQuery } from "./AzureResourceList";
import { AzureResourcePage } from "./AzureResourcePage";
import { AzureTree } from "./AzureTree";
import styles from "./cloud.module.css";

const FILTERS = ["q", "type", "region", "state", "tag", "without"] as const;

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Subscriptions the last pull did not read, each with its own older date. */
function unreadNames(asOf: AzureAsOf): { name: string; at: string | null }[] {
  const since = asOf.last_pull ? new Date(asOf.last_pull.started_at).getTime() : null;
  return asOf.subscriptions
    .filter((s) => !s.readable || (since != null && (!s.last_read_at || new Date(s.last_read_at).getTime() < since)))
    .map((s) => ({ name: s.name, at: s.last_read_at }));
}

export function AzurePanel({ status, onOpenSources }: { status: AzureStatus; onOpenSources: () => void }) {
  const [params, setParams] = useSearchParams();
  const get = (key: string) => params.get(key) ?? "";
  const node = get("node");
  const cat = get("cat");
  const res = get("res");
  const attention = get("attention") as Attention | "";
  const page = Math.max(0, Number(get("page")) || 0);
  const query: ListQuery = { q: get("q"), type: get("type"), region: get("region"), state: get("state"), tag: get("tag"), without: get("without"), removed: get("removed") === "1" };

  /** One change to the query string; "" removes a key. Opening a page pushes history, narrowing replaces. */
  const go = useCallback(
    (patch: Record<string, string>, replace = true) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          return next;
        },
        { replace },
      ),
    [setParams],
  );

  const { state } = useAsync(() => getAzureTree(), [status.as_of.last_pull?.id ?? ""]);

  if (!status.has_data) {
    return status.configured ? (
      <CenteredMessage title="Nothing has been pulled yet" detail="Press Pull now on the Sources tab to read the inventory." action={<Button variant="primary" onClick={onOpenSources}>Open Sources</Button>} />
    ) : (
      <CenteredMessage title="Set up Azure" detail="Azure is not connected yet. An administrator does this under Settings." action={<Link to="/settings/azure"><Button variant="primary">Open Settings</Button></Link>} />
    );
  }
  if (state.status === "loading") return <CenteredMessage title="Loading…" />;
  if (state.status === "error") return <CenteredMessage title="Could not load Azure" detail={state.error.message} />;
  if (!state.data.ok) return <CenteredMessage title="Could not load Azure" detail={state.data.error} />;

  const { tree, attention: counts } = state.data;
  const subs = tree.subscriptions;
  const all = subs.flatMap((s) => [s as TreeNode, ...s.groups]);
  const picked = all.find((n) => n.id === node);
  const sub = subs.find((s) => s.id === node || s.groups.some((g) => g.id === node));
  const isGroup = Boolean(picked && sub && picked.id !== sub.id);
  const lastPull = status.as_of.last_pull;
  const asOf = lastPull ? (lastPull.finished_at ?? lastPull.started_at) : null;
  const since = lastPull ? new Date(lastPull.started_at).getTime() : 0;
  const unread = unreadNames(status.as_of);
  const resources = subs.reduce((n, s) => n + s.count, 0);
  const narrowed = Boolean(cat || attention || FILTERS.some((k) => query[k]) || query.removed);

  const scope: ResourceFilters = {
    ...(picked ? (isGroup ? { resource_group: picked.id } : { subscription: picked.id }) : {}),
    ...(cat ? { category: cat } : {}),
    ...(attention ? { attention } : {}),
  };
  const select = (id: string, c = "") => go({ node: id, cat: c, res: "", page: "" }, false);
  const tile = (key: Attention, label: string, note: string, tone: "warn" | "none" | "accent") => (
    <StatTile label={label} value={counts[key] ?? 0} sub={note} tone={tone} onClick={() => go({ attention: attention === key ? "" : key, res: "", page: "" })} />
  );

  let body;
  if (res) {
    body = <AzureResourcePage id={res} onBack={() => go({ res: "" }, false)} onOpen={(id) => go({ res: id }, false)} onScope={(n, c) => go({ node: n, cat: c ?? "", res: "", page: "" }, false)} />;
  } else if (isGroup && picked && sub && !narrowed) {
    body = <AzureGroup group={picked} subscription={sub} asOf={asOf} onOpen={(id) => go({ res: id }, false)} onCategory={(c) => go({ cat: c, page: "" })} />;
  } else {
    body = (
      <AzureResourceList
        scope={scope}
        query={query}
        page={page}
        since={since}
        onChange={(patch) => go({ ...(patch as Record<string, string>), page: "" })}
        onPage={(p) => go({ page: p ? String(p) : "" })}
        onOpen={(id) => go({ res: id }, false)}
      />
    );
  }

  return (
    <div className={styles.stack}>
      {!status.enabled && <Notice tone="warn">Azure is off. This is the picture from the last pull.</Notice>}
      <p className={styles.asOf}>
        {plural(resources, "Azure resource")} in {plural(subs.length, "subscription")}
        {asOf && (
          <>
            {" "}· as of <DateTime value={asOf} /> (<DateTime value={asOf} mode="relative" />)
          </>
        )}
        {!status.verified_against_real_tenant && <span className={styles.sub}> · not verified against a real tenant</span>}
      </p>
      {unread.length > 0 && (
        <Notice tone="warn">
          Not read in that pull:{" "}
          {unread.map((u, i) => (
            <span key={u.name}>
              {i > 0 && ", "}
              {u.name} (as of <DateTime value={u.at} mode="date" fallback="never" />)
            </span>
          ))}
          .
        </Notice>
      )}
      <div className={styles.tiles}>
        {tile("stopped_failed", "Stopped or failed", "stopped, failed or last deployment failed", "warn")}
        {tile("no_tags", "No tags", "nobody is named as owner", "none")}
        {tile("exposed", "Open to the internet", "worth a look", "warn")}
        {tile("changed", "Changed in the last pull", "added, changed or removed", "accent")}
        {tile("waste", "Possible waste", "attached to nothing, or billed while off", "warn")}
        {(counts.drifted ?? 0) > 0 && tile("drifted", "Drifted", "changed in Azure outside the product, not accepted yet", "warn")}
      </div>
      {status.enabled && status.configured && <AzureCost />}
      <div className={styles.cols}>
        <AzureTree tree={tree} node={node} cat={cat} since={lastPull ? since : null} onSelect={(id, c) => (id ? select(id, c) : go({ node: "", cat: "", res: "", page: "" }, false))} />
        <div className={styles.stack}>{body}</div>
      </div>
    </div>
  );
}
