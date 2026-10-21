/**
 * Instance-wide device inventory: five tabs, a record page and an add / edit page.
 *
 * Changes go through proposals; approve commits them. Permissions hide what a
 * role cannot do — view lists, manage proposes, approve decides and syncs.
 * The screen only routes (tab, device, edit, new live in the URL) and shows the
 * counts; each tab owns its own data.
 */

import { useCallback, useEffect, useMemo, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

import { getAzureStatus } from "@/api/azure";
import { exportInventory } from "@/api/inventory";
import { getInventorySummary } from "@/api/inventorySummary";
import { PERMISSIONS } from "@/api/types";
import { Button, CenteredMessage, Tabs, useToast } from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useSession } from "@/lib/session";
import { AttentionTiles } from "./inventory/AttentionTiles";
import { CloudInventory } from "./inventory/cloud/CloudInventory";
import { DeviceEdit } from "./inventory/DeviceEdit";
import { DeviceRecord } from "./inventory/DeviceRecord";
import { DevicesPanel } from "./inventory/DevicesPanel";
import { saveExport } from "./inventory/download";
import { HealthPanel } from "./inventory/HealthPanel";
import { Segmented } from "./inventory/kit";
import { PlacesPanel } from "./inventory/PlacesPanel";
import { ReviewPanel } from "./inventory/ReviewPanel";
import { SourcesPanel } from "./inventory/SourcesPanel";
import styles from "./InventoryScreen.module.css";
import shell from "./shell.module.css";

const TABS = ["devices", "review", "health", "places", "sources"] as const;
type TabId = (typeof TABS)[number];

/** Tab values the old eight-tab screen used; bookmarks keep working. */
const ALIASES: Record<string, TabId> = {
  pending: "review",
  conflicts: "review",
  lifecycle: "health",
  quality: "health",
  stale: "health",
  sites: "places",
  racks: "places",
};

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function Badge({ count, tone }: { count: number; tone?: "warn" | "crit" }) {
  if (count <= 0) return null;
  return (
    <>
      {" "}
      <span className={`${styles.badge} ${tone ? styles[tone] : ""}`}>{count}</span>
    </>
  );
}

export function InventoryScreen() {
  const session = useSession();
  const toast = useToast();
  const held = useMemo(() => new Set(session.permissions), [session.permissions]);
  const canView = held.has(PERMISSIONS.inventoryView);
  const canManage = held.has(PERMISSIONS.inventoryManage);
  const canApprove = held.has(PERMISSIONS.inventoryApprove);

  const [params, setParams] = useSearchParams();
  const rawTab = params.get("tab") ?? "devices";
  const tab: TabId = ALIASES[rawTab] ?? ((TABS as readonly string[]).includes(rawTab) ? (rawTab as TabId) : "devices");
  const deviceId = params.get("device");
  const editing = canManage && params.get("edit") === "1";
  const adding = canManage && params.get("new") === "1";

  /** One change to the query string; `null` removes a key. Opening a page pushes history, closing one replaces. */
  const go = useCallback(
    (patch: Record<string, string | null>, replace = false) =>
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

  // An old tab name becomes its new place in the address bar; "conflicts" also picks the Conflicts queue.
  useEffect(() => {
    const to = ALIASES[rawTab];
    if (to) go({ tab: to, ...(rawTab === "conflicts" ? { queue: "conflicts" } : {}) }, true);
  }, [rawTab, go]);

  const load = useCallback(() => getInventorySummary(), []);
  const { state, reload } = useAsync(load, [tab, deviceId, adding]);
  const summary = state.status === "ready" && state.data.ok ? state.data : null;

  // The On-prem / Cloud switch exists only once the Azure plugin was turned on or has stored data. Any failure hides it.
  const loadAzure = useCallback(() => getAzureStatus().catch(() => null), []);
  const { state: azureState, reload: reloadAzure } = useAsync(loadAzure, []);
  const azure = azureState.status === "ready" && azureState.data?.ok ? azureState.data : null;
  const showSwitch = Boolean(azure && (azure.enabled || azure.has_data));
  const cloud = azure != null && showSwitch && params.get("scope") === "cloud";

  if (!canView) {
    return <CenteredMessage title="You don't have access to this page" detail="Ask an administrator if you need it." />;
  }

  const scopeSwitch = showSwitch && (
    <Segmented
      label="Inventory scope"
      options={[
        { id: "onprem", label: "On-prem" },
        { id: "cloud", label: "Cloud" },
      ]}
      value={cloud ? "cloud" : "onprem"}
      onChange={(id) => setParams(id === "cloud" ? { scope: "cloud" } : {}, { replace: true })}
    />
  );

  if (cloud) {
    return (
      <div className={shell.stack}>
        <header className={styles.head}>
          <span className={styles.count}>Cloud</span>
          <div className={styles.actions}>{scopeSwitch}</div>
        </header>
        <CloudInventory status={azure} canManage={canManage} onChanged={reloadAzure} />
      </div>
    );
  }

  const labels: Record<TabId, ReactNode> = {
    devices: (
      <>
        Devices
        <Badge count={summary?.devices ?? 0} />
      </>
    ),
    review: (
      <>
        Review
        <Badge count={(summary?.pending ?? 0) + (summary?.conflicts ?? 0)} tone="warn" />
      </>
    ),
    health: (
      <>
        Health
        <Badge count={(summary?.lifecycle.expired ?? 0) + (summary?.lifecycle.soon ?? 0)} tone="crit" />
      </>
    ),
    places: "Places",
    sources: "Sources",
  };

  const openTab = (id: TabId) =>
    go({ tab: id === "devices" ? null : id, device: null, edit: null, new: null, peek: null }, true);
  const openRecord = (id: string) => go({ device: id, edit: null, new: null, peek: null });
  const closeForm = (changed: boolean) => {
    go({ edit: null, new: null }, true);
    if (changed) reload();
  };

  async function download(format: "csv" | "json") {
    try {
      toast.success(`Exported ${saveExport(await exportInventory(format), format === "csv" ? "text/csv" : "application/json")}`);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  const onList = tab === "devices" && !deviceId && !adding;

  let body: ReactNode;
  if (adding) {
    body = <DeviceEdit deviceId={null} canApprove={canApprove} onDone={() => closeForm(true)} onCancel={() => closeForm(false)} />;
  } else if (deviceId && editing) {
    body = <DeviceEdit deviceId={deviceId} onDone={() => closeForm(true)} onCancel={() => closeForm(false)} />;
  } else if (deviceId) {
    body = (
      <DeviceRecord
        deviceId={deviceId}
        canManage={canManage}
        canApprove={canApprove}
        onBack={() => go({ device: null, edit: null })}
        onEdit={() => go({ edit: "1" })}
        onOpenDevice={openRecord}
      />
    );
  } else if (tab === "devices") {
    body = (
      <>
        {summary && <AttentionTiles summary={summary} onGo={openTab} />}
        <DevicesPanel
          canManage={canManage}
          canApprove={canApprove}
          onOpenRecord={openRecord}
          onAdd={() => go({ new: "1", device: null, edit: null })}
        />
      </>
    );
  } else if (tab === "review") {
    body = <ReviewPanel canApprove={canApprove} onChanged={reload} />;
  } else if (tab === "health") {
    body = <HealthPanel canManage={canManage} onOpenDevice={openRecord} />;
  } else if (tab === "places") {
    body = <PlacesPanel canManage={canManage} onOpenDevice={openRecord} />;
  } else {
    body = <SourcesPanel canApprove={canApprove} canManage={canManage} onChanged={reload} />;
  }

  const showAdd = canManage && !adding && !editing && (onList || Boolean(deviceId));

  return (
    <div className={shell.stack}>
      <header className={styles.head}>
        <span className={styles.count}>
          {summary && `${plural(summary.devices, "device")} across ${plural(summary.sites, "site")}`}
        </span>
        <div className={styles.actions}>
          {scopeSwitch}
          {onList && canManage && <Button onClick={() => openTab("sources")}>Import</Button>}
          {onList && <Button onClick={() => void download("csv")}>Export CSV</Button>}
          {onList && <Button onClick={() => void download("json")}>Export JSON</Button>}
          {showAdd && (
            <Button variant="primary" onClick={() => go({ new: "1", device: null, edit: null })}>
              Add device
            </Button>
          )}
        </div>
      </header>
      <Tabs
        label="Inventory"
        items={TABS.map((id) => ({ id, label: labels[id] }))}
        active={tab}
        onChange={(id) => openTab(id as TabId)}
      />
      {body}
    </div>
  );
}
