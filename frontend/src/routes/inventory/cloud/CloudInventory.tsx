/**
 * Inventory > Cloud: Azure, History and Sources. The tab is `ctab` in the address bar; each tab owns its own data.
 * The Cloud view only reads, so nothing here offers to add, edit or review.
 */

import { useSearchParams } from "react-router-dom";

import type { AzureStatus } from "@/api/azure";
import { Tabs } from "@/components";

import { AzureHistory } from "./AzureHistory";
import { AzurePanel } from "./AzurePanel";
import { AzureSources } from "./AzureSources";

const TABS = [
  { id: "azure", label: "Azure" },
  { id: "history", label: "History" },
  { id: "sources", label: "Sources" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function CloudInventory({ status, canManage, onChanged }: { status: AzureStatus; canManage: boolean; onChanged: () => void }) {
  const [params, setParams] = useSearchParams();
  const raw = params.get("ctab");
  const tab: TabId = TABS.some((t) => t.id === raw) ? (raw as TabId) : "azure";

  /** One change to the query string; "" removes a key. */
  const go = (patch: Record<string, string>, replace = true) =>
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
    );

  const openSources = () => go({ ctab: "sources" });

  return (
    <>
      <Tabs label="Cloud inventory" items={[...TABS]} active={tab} onChange={(id) => go({ ctab: id === "azure" ? "" : id })} />
      {tab === "azure" && <AzurePanel status={status} onOpenSources={openSources} />}
      {tab === "history" && <AzureHistory onOpenResource={(id) => go({ ctab: "", res: id }, false)} />}
      {tab === "sources" && <AzureSources canManage={canManage} onChanged={onChanged} />}
    </>
  );
}
