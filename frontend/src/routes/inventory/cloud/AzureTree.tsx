/**
 * Tenant > subscription > resource group > category, with the number of live resources at every level.
 * A subscription Azure no longer lists, or that the last pull did not read, says so under its name.
 */

import { useEffect, useState } from "react";

import { CATEGORIES, CATEGORY_LABELS, type AzureTree as Tree, type TreeNode } from "@/api/azure";
import { DateTime } from "@/components";

import styles from "./cloud.module.css";

type Props = {
  tree: Tree["tree"];
  /** The selected container's id (subscription or resource group) and category. */
  node: string;
  cat: string;
  /** When the pull the data is "as of" started; a subscription last read before it was not in that pull. */
  since: number | null;
  onSelect: (node: string, cat?: string) => void;
};

const total = (tree: Tree["tree"]) => tree.subscriptions.reduce((n, s) => n + s.count, 0);

export function AzureTree({ tree, node, cat, since, onSelect }: Props) {
  const [open, setOpen] = useState(() => new Set(tree.subscriptions.map((s) => s.id)));
  const parentOf = tree.subscriptions.find((s) => s.groups.some((g) => g.id === node));

  // Choosing a group somewhere else (a breadcrumb, a link) opens its path.
  useEffect(() => {
    if (!node) return;
    setOpen((prev) => new Set([...prev, node, ...(parentOf ? [parentOf.id] : [])]));
  }, [node, parentOf]);

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  function row(n: TreeNode, depth: number, expandable: boolean) {
    const on = n.id === node && !cat;
    return (
      <div className={styles.treeRow} style={{ paddingLeft: depth * 16 }}>
        {expandable ? (
          <button type="button" className={styles.twisty} aria-label={`${open.has(n.id) ? "Collapse" : "Expand"} ${n.name}`} aria-expanded={open.has(n.id)} onClick={() => toggle(n.id)}>
            {open.has(n.id) ? "▾" : "▸"}
          </button>
        ) : (
          <span className={styles.twistySpacer} />
        )}
        <button type="button" className={`${styles.treeBtn} ${on ? styles.treeOn : ""}`} aria-current={on ? "true" : undefined} onClick={() => onSelect(n.id)}>
          <span>{n.name}</span>
          <span className={styles.treeCount}>{n.count}</span>
        </button>
      </div>
    );
  }

  const unread = (s: TreeNode) => !s.readable || (since != null && (!s.last_read_at || new Date(s.last_read_at).getTime() < since));

  return (
    <nav className={styles.tree} aria-label="Azure resources">
      <div className={styles.treeRow}>
        <span className={styles.twistySpacer} />
        <button type="button" className={`${styles.treeBtn} ${!node ? styles.treeOn : ""}`} aria-current={!node ? "true" : undefined} onClick={() => onSelect("")}>
          <span>{tree.account?.name || tree.account?.native_id || "Tenant"}</span>
          <span className={styles.treeCount}>{total(tree)}</span>
        </button>
      </div>
      {tree.subscriptions.map((sub) => (
        <div key={sub.id}>
          {row(sub, 1, sub.groups.length > 0)}
          {unread(sub) && (
            <div className={styles.treeWarn} role="note">
              {sub.readable ? "Not read in the last pull, as of " : "Not readable since "}
              <DateTime value={sub.last_read_at} mode="date" fallback="the first pull" />
            </div>
          )}
          {open.has(sub.id) &&
            sub.groups.map((group) => (
              <div key={group.id}>
                {row(group, 2, group.count > 0)}
                {open.has(group.id) &&
                  CATEGORIES.filter((c) => (group.categories[c] ?? 0) > 0).map((c) => {
                    const on = group.id === node && cat === c;
                    return (
                      <div key={c} className={styles.treeRow} style={{ paddingLeft: 3 * 16 }}>
                        <span className={styles.twistySpacer} />
                        <button type="button" className={`${styles.treeBtn} ${on ? styles.treeOn : ""}`} aria-current={on ? "true" : undefined} onClick={() => onSelect(group.id, c)}>
                          <span>{CATEGORY_LABELS[c]}</span>
                          <span className={styles.treeCount}>{group.categories[c]}</span>
                        </button>
                      </div>
                    );
                  })}
              </div>
            ))}
        </div>
      ))}
    </nav>
  );
}
