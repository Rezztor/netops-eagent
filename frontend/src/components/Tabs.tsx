/**
 * An accessible tab bar: `role="tablist"`, roving tabindex, arrow-key movement
 * and `aria-controls` wiring.
 *
 * The Network screen (and others) rolled their own tab bars out of buttons with
 * no roles, no keyboard model and no association to the panel they switched.
 * This is the shared primitive; a screen supplies the items and renders the
 * matching `<TabPanel>` for the active id.
 */

import { useId, useRef, type ReactNode } from "react";

import styles from "./foundations.module.css";

export interface TabItem {
  id: string;
  label: ReactNode;
}

export function Tabs({
  items,
  active,
  onChange,
  label,
}: {
  items: TabItem[];
  active: string;
  onChange: (id: string) => void;
  label: string;
}) {
  const baseId = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const tabId = (id: string) => `${baseId}-tab-${id}`;
  const panelId = (id: string) => `${baseId}-panel-${id}`;

  const onKeyDown = (event: React.KeyboardEvent) => {
    const index = items.findIndex((item) => item.id === active);
    if (index < 0) return;
    let next = index;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % items.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp")
      next = (index - 1 + items.length) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else return;
    event.preventDefault();
    const target = items[next];
    if (!target) return;
    onChange(target.id);
    refs.current[target.id]?.focus();
  };

  return (
    <div className={styles.tabs} role="tablist" aria-label={label} onKeyDown={onKeyDown}>
      {items.map((item) => {
        const selected = item.id === active;
        return (
          <button
            key={item.id}
            ref={(node) => {
              refs.current[item.id] = node;
            }}
            type="button"
            role="tab"
            id={tabId(item.id)}
            aria-selected={selected}
            aria-controls={panelId(item.id)}
            tabIndex={selected ? 0 : -1}
            className={`${styles.tab} ${selected ? styles.tabActive : ""}`}
            onClick={() => onChange(item.id)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The panel for a tab. `tabsId` must match the `Tabs` instance; pass the same
 * `useId()` base if you need the ids to line up exactly, otherwise rely on the
 * `aria-labelledby` reference by id string below.
 */
export function TabPanel({
  id,
  active,
  labelledBy,
  children,
}: {
  id: string;
  active: string;
  labelledBy?: string;
  children: ReactNode;
}) {
  if (id !== active) return null;
  return (
    <div role="tabpanel" aria-labelledby={labelledBy} tabIndex={0}>
      {children}
    </div>
  );
}
