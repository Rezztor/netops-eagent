/** A resource group, opened by category. A category with nothing in it is not shown. */

import { CATEGORIES, CATEGORY_LABELS, listAzureResources, type CloudCategory, type TreeNode } from "@/api/azure";
import { Button, CenteredMessage, DateTime } from "@/components";
import { useAsync } from "@/lib/useAsync";

import { StateTag, TagChips } from "./AzureResourceList";
import { Section } from "../kit";
import styles from "./cloud.module.css";

const SHOWN = 4;
const FETCH = 200;

export function AzureGroup({
  group,
  subscription,
  asOf,
  onOpen,
  onCategory,
}: {
  group: TreeNode;
  subscription: TreeNode;
  asOf: string | null;
  onOpen: (id: string) => void;
  onCategory: (category: CloudCategory) => void;
}) {
  // ponytail: one request for the first 200 resources; a category past that shows its count and "more" only.
  const { state } = useAsync(() => listAzureResources({ resource_group: group.id, limit: FETCH }), [group.id]);
  if (state.status === "error") return <CenteredMessage title="Could not load the resource group" detail={state.error.message} />;
  const resources = state.status === "ready" && state.data.ok ? state.data.resources : [];

  return (
    <div className={styles.stack}>
      <div className={styles.inner}>
        <div className={styles.crumbs}>
          <span>{subscription.name}</span>›<strong>{group.name}</strong>
        </div>
        <p className={styles.asOf}>
          Resource group · {group.region || "no region"} · {group.count} resources
          {asOf && (
            <>
              {" "}· as of <DateTime value={asOf} />
            </>
          )}
        </p>
        <TagChips tags={group.tags} max={8} />
      </div>
      {CATEGORIES.filter((c) => (group.categories[c] ?? 0) > 0).map((c) => {
        const count = group.categories[c] ?? 0;
        const mine = resources.filter((r) => r.category === c);
        return (
          <Section key={c} title={`${CATEGORY_LABELS[c]} ${count}`}>
            <div>
              {mine.slice(0, SHOWN).map((r) => (
                <div key={r.id} className={styles.line}>
                  <span>
                    <button type="button" className={`${styles.link} ${styles.name}`} onClick={() => onOpen(r.id)}>{r.name}</button>{" "}
                    <span className={styles.dim}>{[r.label, (r.sku as { name?: string }).name].filter(Boolean).join(" · ")}</span>
                  </span>
                  <StateTag resource={r} />
                </div>
              ))}
            </div>
            {count > Math.min(mine.length, SHOWN) && (
              <div>
                <Button variant="ghost" onClick={() => onCategory(c)}>{`${count - Math.min(mine.length, SHOWN)} more`}</Button>
              </div>
            )}
          </Section>
        );
      })}
    </div>
  );
}
