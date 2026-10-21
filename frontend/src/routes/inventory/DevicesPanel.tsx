/**
 * The Devices tab: toolbar, column sets, the table, the quick summary beside it, and the bulk bar.
 * Changes still go through proposals; nothing here writes a device directly.
 */

import { useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

import { listDevices, syncInventory, type Device, type InventoryFacets, type SyncDiff } from "@/api/inventory";
import {
  Button,
  CenteredMessage,
  DateTime,
  EmptyRow,
  Modal,
  Mono,
  Pagination,
  ScopeNote,
  SearchInput,
  Table,
  Tag,
  useToast,
} from "@/components";
import { listPlaces } from "@/api/inventoryPlaces";
import { PERMISSIONS } from "@/api/types";
import { useCan } from "@/lib/session";
import { useAsync } from "@/lib/useAsync";
import { useUrlState } from "@/lib/useUrlState";

import { BulkEditDialog } from "../InventoryBulkEdit";
import net from "../network.module.css";
import { DeviceSummary, placeOf, statusTone, titleCase } from "./DeviceSummary";
import { Meter, Segmented, lifecycleSummary } from "./kit";
import { PlaceSearch } from "./PlacePicker";
import { VlanFinder } from "./VlanFinder";
import styles from "./DevicesPanel.module.css";

const PAGE = 50;
const COLUMNS_KEY = "eagent.inventory.columns";

interface Column {
  id: string;
  label: string;
  cell: (d: Device) => ReactNode;
}

/** Days since last_seen for a stale source badge ("stale 9d"). */
function staleAgeLabel(device: Device): string | null {
  const stale = (device.sources ?? []).filter((s) => s.stale && s.last_seen_at);
  if (!stale.length) return null;
  const oldest = Math.min(...stale.map((s) => new Date(s.last_seen_at!).getTime()));
  return `stale ${Math.max(0, Math.floor((Date.now() - oldest) / 86_400_000))}d`;
}

const text = (value?: string | null) => value || "—";
const unset = <span className={styles.unset}>Not set</span>;

/** Every column the table can show; the device name is always shown and is not listed here. */
const COLUMNS: Column[] = [
  {
    id: "type",
    label: "Type",
    cell: (d) =>
      d.device_type ? (
        <div className={styles.stack2}>
          <span>{titleCase(d.device_type)}</span>
          {d.role && <span className={styles.sub}>{d.role}</span>}
        </div>
      ) : (
        unset
      ),
  },
  { id: "place", label: "Place", cell: (d) => placeOf(d) || unset },
  { id: "mgmt_ip", label: "Management IP", cell: (d) => (d.mgmt_ip ? <Mono>{d.mgmt_ip}</Mono> : unset) },
  { id: "software", label: "Software", cell: (d) => <span className={styles.dim}>{text(d.software_version)}</span> },
  {
    id: "status",
    label: "Status",
    cell: (d) => {
      const stale = staleAgeLabel(d);
      return (
        <>
          <Tag tone={statusTone(d.status)}>{d.status}</Tag>
          {stale && (
            <Tag tone="warn">
              <span data-testid={`stale-${d.id}`}>{stale}</span>
            </Tag>
          )}
        </>
      );
    },
  },
  {
    id: "lifecycle",
    label: "Lifecycle",
    cell: (d) => {
      const { text: words, tone } = lifecycleSummary(d.computed);
      return <span className={tone === "crit" ? styles.crit : tone === "warn" ? styles.warn : styles.dim}>{words}</span>;
    },
  },
  {
    id: "record",
    label: "Record",
    cell: (d) => {
      const percent = d.computed?.completeness.percent;
      return percent == null ? "—" : <Meter percent={percent} tone={percent >= 95 ? "ok" : percent >= 50 ? "warn" : "crit"} />;
    },
  },
  { id: "vendor", label: "Vendor", cell: (d) => text(d.vendor) },
  { id: "platform", label: "Platform", cell: (d) => text(d.platform) },
  { id: "model", label: "Model", cell: (d) => text(d.model) },
  { id: "serial", label: "Serial", cell: (d) => (d.serial ? <Mono>{d.serial}</Mono> : "—") },
  { id: "role", label: "Role", cell: (d) => text(d.role) },
  { id: "part_number", label: "Part number", cell: (d) => text(d.part_number) },
  { id: "base_mac", label: "Base MAC", cell: (d) => text(d.base_mac) },
  { id: "site", label: "Site", cell: (d) => text(d.site?.name) },
  { id: "location", label: "Location", cell: (d) => text(d.location) },
  { id: "building", label: "Building", cell: (d) => text(d.building) },
  { id: "floor", label: "Floor", cell: (d) => text(d.floor) },
  { id: "room", label: "Room", cell: (d) => text(d.room) },
  { id: "rack", label: "Rack", cell: (d) => text(d.rack ? (d.rack_unit ? `${d.rack} U${d.rack_unit}` : d.rack) : "") },
  { id: "place_id", label: "Place ID", cell: (d) => (d.place_id ? <Mono>{d.place_id}</Mono> : unset) },
  { id: "department", label: "Department", cell: (d) => text(d.department) },
  { id: "purchase", label: "Purchased", cell: (d) => text(d.purchase_date) },
  { id: "warranty", label: "Warranty expires", cell: (d) => text(d.warranty_expiry) },
  { id: "support", label: "Support expires", cell: (d) => text(d.support_expiry) },
  { id: "eol", label: "End of life", cell: (d) => text(d.eol_date) },
  { id: "updated", label: "Updated", cell: (d) => <DateTime value={d.updated_at} mode="relative" /> },
];

const VIEWS = {
  overview: ["type", "place", "mgmt_ip", "software", "status", "lifecycle", "record"],
  hardware: ["vendor", "platform", "model", "serial", "part_number", "base_mac", "software", "status"],
  place: ["site", "location", "building", "floor", "room", "rack", "place_id", "status"],
  lifecycle: ["purchase", "warranty", "support", "eol", "lifecycle", "status"],
} as const;
type View = keyof typeof VIEWS;
const VIEW_OPTIONS = [
  { id: "overview", label: "Overview" },
  { id: "hardware", label: "Hardware" },
  { id: "place", label: "Place" },
  { id: "lifecycle", label: "Lifecycle" },
] as const;

/** With the summary open the table gives up the long columns instead of scrolling sideways. */
const NARROW_DROPS = new Set(["mgmt_ip", "software", "record", "serial", "part_number", "base_mac", "updated", "location", "building", "floor", "place_id", "department"]);

function loadColumns(): string[] | null {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(COLUMNS_KEY) ?? "null");
    const ids = Array.isArray(saved) ? saved.filter((id) => COLUMNS.some((c) => c.id === id)) : [];
    return ids.length ? ids : null;
  } catch {
    return null; // storage blocked or corrupt: use the column set
  }
}

/** Facet values plus the current one, so a URL-held filter never shows as blank. */
const withCurrent = (list: string[] | undefined, current: string) =>
  current && !(list ?? []).includes(current) ? [...(list ?? []), current] : (list ?? []);

function Chip({
  label,
  name,
  value,
  onChange,
  allLabel,
  options,
}: {
  label: string;
  name: string;
  value: string;
  onChange: (next: string) => void;
  allLabel: string;
  options: { value: string; label: string }[];
}) {
  return (
    <label className={`${styles.chip} ${value ? styles.chipOn : ""}`}>
      <span>{label}</span>
      <select aria-label={`Filter by ${name}`} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{allLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

const plain = (list: string[]) => list.map((v) => ({ value: v, label: v }));

function downloadText(filename: string, content: string, contentType: string) {
  const url = URL.createObjectURL(new Blob([content], { type: contentType }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function DevicesPanel({
  canManage,
  canApprove,
  onOpenRecord,
  onAdd,
}: {
  canManage: boolean;
  canApprove: boolean;
  onOpenRecord: (id: string) => void;
  onAdd: () => void;
}) {
  const toast = useToast();
  const canPlaces = useCan(PERMISSIONS.inventoryPlaces);
  const [, setParams] = useSearchParams();
  const [q, setQ] = useUrlState("q");
  const [placeId, setPlaceId] = useUrlState("place");
  const [deviceType, setDeviceType] = useUrlState("type");
  const [status, setStatus] = useUrlState("status");
  const [role, setRole] = useUrlState("role");
  const [department, setDepartment] = useUrlState("department");
  const [rawView, setView] = useUrlState("view", "overview");
  const [peek, setPeek] = useUrlState("peek");
  const [vendor, setVendor] = useState("");
  const [platform, setPlatform] = useState("");
  const [tag, setTag] = useState("");
  const [staleOnly, setStaleOnly] = useState(false);
  const [custom, setCustom] = useState(loadColumns);
  const [menu, setMenu] = useState<"" | "place" | "filter" | "columns" | "more">("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState<"" | "fields" | "status" | "tag">("");
  const [syncDiff, setSyncDiff] = useState<SyncDiff | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const [findVlan, setFindVlan] = useState(false);
  const [pageAt, setPageAt] = useState({ key: "", n: 0 });

  const view = (rawView in VIEWS ? rawView : "overview") as View;
  const base = useMemo(
    () => ({
      ...(q.trim() ? { q: q.trim() } : {}),
      ...(placeId ? { place: placeId } : {}),
      ...(vendor ? { vendor } : {}),
      ...(platform ? { platform } : {}),
      ...(status ? { status } : {}),
      ...(tag ? { tag } : {}),
      ...(deviceType ? { device_type: deviceType } : {}),
      ...(role ? { role } : {}),
      ...(department ? { department } : {}),
      ...(staleOnly ? { stale: true } : {}),
    }),
    [q, placeId, vendor, platform, status, tag, deviceType, role, department, staleOnly],
  );
  const key = JSON.stringify(base);
  const page = pageAt.key === key ? pageAt.n : 0; // a new filter starts on page one
  const filters = { ...base, limit: PAGE, offset: page * PAGE };

  const { state, reload } = useAsync(() => listDevices(filters), [key, page]);
  const placesState = useAsync(() => listPlaces(true), []);
  const places = placesState.state.status === "ready" && placesState.state.data.ok ? placesState.state.data.places : [];

  function chooseColumns(next: string[]) {
    setCustom(next);
    try {
      localStorage.setItem(COLUMNS_KEY, JSON.stringify(next));
    } catch {
      /* the choice still applies for this visit */
    }
  }

  function pickView(next: View) {
    setView(next);
    setCustom(null);
    try {
      localStorage.removeItem(COLUMNS_KEY);
    } catch {
      /* nothing stored to clear */
    }
  }

  function edit(id: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("device", id);
      next.set("edit", "1");
      return next;
    });
  }

  async function syncCall(dryRun: boolean) {
    setSyncBusy(true);
    try {
      const result = await syncInventory(dryRun);
      if (!result.ok || (dryRun && !result.diff)) throw new Error(result.error || (dryRun ? "Sync preview failed." : "Sync failed."));
      if (dryRun) setSyncDiff(result.diff!);
      else {
        setSyncDiff(null);
        toast.success(result.applied ? "Executor inventory updated." : "Sync completed with no apply.");
        reload();
      }
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSyncBusy(false);
    }
  }

  if (state.status === "loading") return <CenteredMessage title="Loading inventory…" />;
  if (state.status === "error" || !state.data.ok) {
    return (
      <CenteredMessage
        title={state.status === "error" ? "Could not load inventory" : "Inventory refused"}
        detail={state.status === "error" ? state.error.message : state.data.error}
        action={
          <Button variant="primary" onClick={reload}>
            Retry
          </Button>
        }
      />
    );
  }

  const facets: InventoryFacets = state.data.facets ?? { vendor: [], platform: [], status: [], site: [], tag: [] };
  const all = state.data.devices ?? [];
  const devices = status ? all : all.filter((d) => d.status !== "decommissioned");
  const total = state.data.total ?? all.length;
  const selected = devices.filter((d) => checked.has(d.id));
  const peeked = devices.find((d) => d.id === peek);
  const ids: readonly string[] = custom ?? VIEWS[view];
  const shown = COLUMNS.filter((c) => ids.includes(c.id) && !(peeked && NARROW_DROPS.has(c.id)));
  const statuses = [...new Set([...(facets.statuses ?? facets.status), "decommissioned"])];
  const types = facets.device_type?.length ? facets.device_type : (facets.device_types ?? []);
  const extra = [vendor, platform, tag, role, department, staleOnly ? "stale" : ""].filter(Boolean).length;
  const toggle = (name: typeof menu) => setMenu(menu === name ? "" : name);
  const pageCount = Math.ceil(total / PAGE);

  const pick = (id: string, on: boolean) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  return (
    <div className={styles.stack}>
      {selected.length > 0 && canManage ? (
        <div role="toolbar" aria-label="Actions for selected devices" className={styles.bulk}>
          <div className={styles.group}>
            <strong>{`${selected.length} device${selected.length === 1 ? "" : "s"} selected`}</strong>
            <Button onClick={() => setChecked(new Set())}>Clear</Button>
          </div>
          <div className={styles.group}>
            <Button onClick={() => setBulk("fields")}>Edit fields</Button>
            <Button onClick={() => setBulk("status")}>Change status</Button>
            <Button onClick={() => setBulk("tag")}>Add tag</Button>
            <Button onClick={() => downloadText("inventory-selected.json", JSON.stringify(selected, null, 2), "application/json")}>
              Export selected
            </Button>
          </div>
        </div>
      ) : (
        <div className={styles.toolbar}>
          <div className={styles.group}>
            <div className={styles.search}>
              <SearchInput value={q} onChange={setQ} placeholder="Search name, serial, IP, place" label="Search devices" />
            </div>
            <div className={styles.menuWrap}>
              <Button aria-expanded={menu === "place"} onClick={() => toggle("place")}>
                {placeId ? `Place: ${placeId === "none" ? "No place yet" : placeId}` : "Place: All"}
              </Button>
              {menu === "place" && (
                <div className={`${styles.menu} ${styles.menuLeft}`}>
                  <PlaceSearch
                    places={places}
                    onPick={(id) => {
                      setPlaceId(id);
                      setMenu("");
                    }}
                  />
                  <Button
                    onClick={() => {
                      setPlaceId("none");
                      setMenu("");
                    }}
                  >
                    No place yet
                  </Button>
                  {placeId && (
                    <Button
                      onClick={() => {
                        setPlaceId("");
                        setMenu("");
                      }}
                    >
                      All places
                    </Button>
                  )}
                </div>
              )}
            </div>
            <Chip label="Type" name="type" value={deviceType} onChange={setDeviceType} allLabel="All" options={plain(withCurrent(types, deviceType))} />
            <Chip label="Status" name="status" value={status} onChange={setStatus} allLabel="All but decommissioned" options={plain(withCurrent(statuses, status))} />
            <div className={styles.menuWrap}>
              <Button variant="ghost" aria-expanded={menu === "filter"} onClick={() => toggle("filter")}>
                {extra ? `Filter (${extra})` : "Filter"}
              </Button>
              {menu === "filter" && (
                <div className={`${styles.menu} ${styles.menuLeft}`}>
                  {(
                    [
                      ["role", role, setRole, facets.role],
                      ["department", department, setDepartment, facets.department],
                      ["vendor", vendor, setVendor, facets.vendor],
                      ["platform", platform, setPlatform, facets.platform],
                      ["tag", tag, setTag, facets.tag],
                    ] as const
                  ).map(([name, value, set, list]) => (
                    <label key={name}>
                      {titleCase(name)}
                      <select aria-label={`Filter by ${name}`} value={value} onChange={(e) => set(e.target.value)}>
                        <option value="">All</option>
                        {withCurrent(list, value).map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <label className={styles.inline}>
                    <input type="checkbox" aria-label="Stale only" checked={staleOnly} onChange={(e) => setStaleOnly(e.target.checked)} />
                    Not seen recently
                  </label>
                </div>
              )}
            </div>
          </div>
          <div className={styles.group}>
            <Segmented label="Column set" options={[...VIEW_OPTIONS]} value={view} onChange={pickView} />
            <div className={styles.menuWrap}>
              <Button aria-label="Choose columns" aria-expanded={menu === "columns"} onClick={() => toggle("columns")}>
                Columns
              </Button>
              {menu === "columns" && (
                <div className={styles.menu}>
                  <div className={styles.columns}>
                    {COLUMNS.map((c) => (
                      <label key={c.id} className={styles.inline}>
                        <input
                          type="checkbox"
                          aria-label={`Column ${c.label}`}
                          checked={ids.includes(c.id)}
                          onChange={(e) => chooseColumns(e.target.checked ? [...ids, c.id] : ids.filter((id) => id !== c.id))}
                        />
                        {c.label}
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className={styles.menuWrap}>
              <Button aria-label="More actions" aria-expanded={menu === "more"} onClick={() => toggle("more")}>
                ⋯
              </Button>
              {menu === "more" && (
                <div className={styles.menu} role="menu">
                  {canApprove && (
                    <button
                      type="button"
                      role="menuitem"
                      className={styles.menuItem}
                      disabled={syncBusy}
                      onClick={() => {
                        setMenu("");
                        void syncCall(true);
                      }}
                    >
                      Sync to executor
                    </button>
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    className={styles.menuItem}
                    onClick={() => {
                      setMenu("");
                      reload();
                    }}
                  >
                    Refresh
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className={styles.menuItem}
                    onClick={() => {
                      setMenu("");
                      setFindVlan(true);
                    }}
                  >
                    Find a VLAN
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className={styles.body}>
        <div className={styles.tableWrap}>
          <Table>
            <thead>
              <tr>
                {canManage && (
                  <th className={styles.check}>
                    <input
                      type="checkbox"
                      aria-label="Select all"
                      checked={devices.length > 0 && selected.length === devices.length}
                      onChange={(e) => setChecked(new Set(e.target.checked ? devices.map((d) => d.id) : []))}
                    />
                  </th>
                )}
                <th>Device</th>
                {shown.map((c) => (
                  <th key={c.id}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {devices.length === 0 ? (
                <EmptyRow colSpan={shown.length + (canManage ? 2 : 1)}>
                  No devices yet. Add a device to create a proposal; an engineer who can approve inventory changes commits it.
                  {canManage && (
                    <div style={{ marginTop: "var(--space-3)" }}>
                      <Button variant="primary" onClick={onAdd}>
                        Add device
                      </Button>
                    </div>
                  )}
                </EmptyRow>
              ) : (
                devices.map((d) => (
                  <tr key={d.id} className={`${styles.row} ${d.id === peek ? styles.rowOpen : ""}`} onClick={() => setPeek(d.id)}>
                    {canManage && (
                      <td className={styles.check} onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" aria-label={`Select ${d.name}`} checked={checked.has(d.id)} onChange={(e) => pick(d.id, e.target.checked)} />
                      </td>
                    )}
                    <td>
                      <button type="button" className={styles.nameBtn} aria-label={`Show summary of ${d.name}`}>
                        <Mono>{d.name}</Mono>
                        <span className={styles.sub}>{[d.vendor, d.model].filter(Boolean).join(" ")}</span>
                      </button>
                    </td>
                    {shown.map((c) => (
                      <td key={c.id}>{c.cell(d)}</td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </Table>
          <div className={styles.footer}>
            <div>
              <Pagination
                page={page}
                pageCount={pageCount}
                onPage={(n) => setPageAt({ key, n })}
                total={total}
                matched={devices.length}
                noun="devices"
              />
            </div>
            {!status && all.length > devices.length && <span>decommissioned devices are hidden</span>}
          </div>
        </div>
        {peeked && (
          <DeviceSummary
            device={peeked}
            places={places}
            canManage={canManage}
            onClose={() => setPeek("")}
            onOpenRecord={() => onOpenRecord(peeked.id)}
            onEdit={() => edit(peeked.id)}
          />
        )}
      </div>

      {bulk && canManage && (
        <BulkEditDialog
          devices={selected.map((d) => ({ id: d.id, name: d.name, tags: d.tags }))}
          canCreate={{ upper: canPlaces, lower: canManage }}
          only={bulk === "status" ? ["status"] : bulk === "tag" ? ["tags"] : undefined}
          onClose={() => setBulk("")}
          onDone={(batchId) => {
            setBulk("");
            setChecked(new Set());
            toast.success(`Bulk edit sent to Review (batch ${batchId.slice(0, 8)}).`);
            reload();
          }}
        />
      )}

      {findVlan && (
        <VlanFinder
          onClose={() => setFindVlan(false)}
          onOpenRecord={(id) => {
            setFindVlan(false);
            onOpenRecord(id);
          }}
        />
      )}

      {syncDiff && canApprove && (
        <Modal
          title="Sync to executor"
          onClose={() => setSyncDiff(null)}
          footer={
            <>
              <Button onClick={() => setSyncDiff(null)}>Cancel</Button>
              <Button variant="primary" disabled={syncBusy} onClick={() => void syncCall(false)}>
                Apply sync
              </Button>
            </>
          }
        >
          <div style={{ display: "grid", gap: "var(--space-2)" }}>
            {(["added", "removed", "changed"] as const).map((k) => (
              <div key={k}>
                <strong>{titleCase(k)}</strong>
                <pre className={net.pre}>{syncDiff[k].join("\n") || "(none)"}</pre>
              </div>
            ))}
            {syncDiff.warnings.length > 0 && (
              <ScopeNote tone="warn">
                <div>{syncDiff.warnings.join("; ")}</div>
              </ScopeNote>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
