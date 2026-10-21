/** The full record of one device: summary tiles, the six fact cards, recent changes and the lifecycle timeline. */

import { useCallback, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { getDevice, proposeChange, type Device, type Proposal } from "@/api/inventory";
import { getPorts } from "@/api/inventoryPorts";
import { listPlaces } from "@/api/inventoryPlaces";
import { getRacks } from "@/api/inventoryReports";
import { getNetworks } from "@/api/networkConsole";
import { Button, CenteredMessage, DateTime, Mono, Tag, useConfirm, useToast } from "@/components";
import { useAsync } from "@/lib/useAsync";
import { FactsSection, HaSection, useFactsRefresh } from "../InventoryFacts";
import { DeviceBackups } from "./DeviceBackups";
import { DevicePorts } from "./DevicePorts";
import { PlacePath } from "./PlacePicker";
import { KV, Section, StatTile, fieldLabel, lifecycleSummary, sourceLabel, type KVRow, type Tone } from "./kit";
import styles from "./DeviceRecord.module.css";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const fmtDate = (value: string | Date) => {
  const d = typeof value === "string" ? new Date(`${value}T00:00:00`) : value;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
};

const statusTone = (status: string) =>
  status === "active"
    ? "ok"
    : ["offline", "faulty", "decommissioned"].includes(status)
      ? "crit"
      : ["staged", "planned"].includes(status)
        ? "warn"
        : "muted";

const typeLabel = (type: string) => {
  const text = (type || "other").replace(/_/g, " ");
  return text[0]!.toUpperCase() + text.slice(1);
};

const mono = (value?: string | null): ReactNode => (value ? <Mono>{value}</Mono> : "");

const show = (v: unknown) =>
  v == null || v === "" ? "empty" : Array.isArray(v) ? v.join(", ") : String(v);

/** One line for a proposal: what it did to the device. */
function changeText(p: Proposal): string {
  if (p.kind === "create") return "Created";
  if (p.kind === "decommission") return "Decommission";
  const text = Object.entries(p.changes)
    .map(([field, c]) => `${field.replace(/_/g, " ")} ${show(c.old)} to ${show(c.new)}`)
    .join("; ");
  return text ? text[0]!.toUpperCase() + text.slice(1) : "Updated";
}

function ChangeRows({ proposals }: { proposals: Proposal[] }) {
  return (
    <div>
      {proposals.map((p) => (
        <div key={p.id} className={styles.change}>
          <span className={styles.changeText}>
            {changeText(p)}{" "}
            {p.status !== "approved" && <Tag tone={p.status === "pending" ? "warn" : "muted"}>{p.status}</Tag>}
          </span>
          <span className={styles.changeMeta}>
            {p.requested_by}
            {p.source !== "manual" && `, ${p.source}`} · <DateTime value={p.requested_at} mode="relative" />
          </span>
        </div>
      ))}
    </div>
  );
}

/** The device and its neighbours in the rack, as in the Places drawing. */
function RackStrip({ device }: { device: Device }) {
  const load = useCallback(() => getRacks(device.place_id ? { place: device.place_id } : {}), [device.place_id]);
  const { state } = useAsync(load, [device.place_id]);
  if (state.status !== "ready") return null;
  const group = state.data.racks?.find((g) => g.devices.some((d) => d.device_id === device.id));
  const placed = (group?.devices ?? [])
    .filter((d) => d.rack_unit != null)
    .sort((a, b) => b.rack_unit! - a.rack_unit!);
  const at = placed.findIndex((d) => d.device_id === device.id);
  if (at < 0) return null;
  // ponytail: two above, one below; the full drawing lives on Places
  return (
    <div className={styles.rack} data-testid="rack-strip">
      {placed.slice(Math.max(0, at - 2), at + 2).map((d) => (
        <span key={d.device_id} className={d.device_id === device.id ? styles.rackNow : styles.rackU}>
          U{d.rack_unit} {d.name}
        </span>
      ))}
    </div>
  );
}

function Timeline({ device }: { device: Device }) {
  const life = device.computed?.lifecycle;
  const points: { label: string; date: string; state: string }[] = (
    [
      ["Purchased", device.purchase_date, "none"],
      ["Warranty ends", device.warranty_expiry, life?.warranty.state],
      ["Support ends", device.support_expiry, life?.support.state],
      ["End of life", device.eol_date, life?.eol.state],
    ] as const
  ).flatMap(([label, date, state]) => (date ? [{ label, date, state: state ?? "none" }] : []));
  if (!points.length) return <span className={styles.note}>No lifecycle dates are set.</span>;

  const times = points.map((p) => new Date(`${p.date}T00:00:00`).getTime());
  const [first, last] = [Math.min(...times), Math.max(...times)];
  const pct = (t: number) => (last > first ? Math.max(0, Math.min(100, ((t - first) / (last - first)) * 100)) : 0);
  const now = new Date();
  return (
    <div className={styles.timeline}>
      {points.length > 1 && (
        <div className={styles.track} data-testid="lifecycle-track">
          <span className={styles.trackFill} style={{ width: `${pct(now.getTime())}%` }} />
          <span className={styles.today} style={{ left: `${pct(now.getTime())}%` }} />
          {points.map((p, i) => (
            <span
              key={p.label}
              className={`${styles.dot} ${p.state === "expired" ? styles.dotCrit : p.state === "soon" ? styles.dotWarn : ""}`}
              style={{ left: `${pct(times[i]!)}%` }}
            />
          ))}
        </div>
      )}
      <div className={styles.dates}>
        {points.map((p) => (
          <div key={p.label}>
            <span className={`${styles.dateLabel} ${p.state === "expired" ? styles.crit : p.state === "soon" ? styles.warn : ""}`}>
              {p.label}
            </span>
            <span className={styles.dateValue}>{fmtDate(p.date)}</span>
          </div>
        ))}
      </div>
      <span className={styles.note}>The mark is today, {fmtDate(now)}.</span>
    </div>
  );
}

export function DeviceRecord({
  deviceId,
  canManage,
  onBack,
  onEdit,
  onOpenDevice,
}: {
  deviceId: string;
  canManage: boolean;
  canApprove: boolean;
  onBack: () => void;
  onEdit: () => void;
  onOpenDevice: (id: string) => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const load = useCallback(() => getDevice(deviceId), [deviceId]);
  const { state, reload } = useAsync(load, [deviceId]);
  const [sub, setSub] = useState<"overview" | "ports" | "backups" | "changes">("overview");
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const loadPorts = useCallback(() => getPorts(deviceId), [deviceId]);
  const ports = useAsync(loadPorts, [deviceId]);
  const placesState = useAsync(() => listPlaces(true), []);
  const places = placesState.state.status === "ready" && placesState.state.data.ok ? placesState.state.data.places : [];
  const loaded = state.status === "ready" ? state.data.device : undefined;
  const facts = useFactsRefresh(loaded?.name ?? "", () => {
    reload();
    ports.reload();
  });
  // "VLAN 200, routed", worked out from the address by the appliance's network list; nothing is
  // stored on the device. A caller without `network.view`, or an appliance with no list, gets no label.
  const address = loaded?.mgmt_ip ?? "";
  const network = useAsync(
    () => (address ? getNetworks([address]).catch(() => null) : Promise.resolve(null)),
    [address],
  );
  const networkLabel = network.state.status === "ready" ? network.state.data?.labels?.[address] : "";
  const portsData = ports.state.status === "ready" && ports.state.data.ok ? ports.state.data : null;
  const uplink = portsData?.summary?.uplinks[0];

  if (state.status === "loading") return <CenteredMessage title="Loading…" />;
  if (state.status === "error" || !state.data.ok || !state.data.device) {
    return (
      <CenteredMessage
        title="Could not load device"
        detail={state.status === "error" ? state.error.message : state.data.error}
        action={<Button onClick={onBack}>Back to devices</Button>}
      />
    );
  }

  const device = state.data.device;
  const proposals = [...(state.data.proposals ?? [])].sort((a, b) => b.requested_at.localeCompare(a.requested_at));
  const c = device.computed;
  const src = (field: string) => sourceLabel(device.field_sources?.[field]);
  const row = (label: string, value: ReactNode, field?: string): KVRow => ({
    label,
    value,
    source: field ? src(field) : "",
  });

  async function decommission() {
    setMenuOpen(false);
    const ok = await confirm({
      title: `Decommission ${device.name}?`,
      body: "Creates a decommission proposal. The device is never deleted.",
      danger: true,
      confirmLabel: "Propose decommission",
    });
    if (!ok) return;
    setBusy(true);
    try {
      const result = await proposeChange({
        kind: "decommission",
        device_id: device.id,
        fields: { status: "decommissioned" },
      });
      if (!result.ok) throw new Error(result.error || "Propose failed.");
      toast.success("Decommission proposed.");
      reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  const place = [
    device.site?.name,
    device.room && `room ${device.room}`,
    device.rack && `rack ${device.rack}${device.rack_unit != null ? ` U${device.rack_unit}` : ""}`,
  ].filter(Boolean);
  const hardware = [device.vendor, device.model].filter(Boolean).join(" ");

  const life = lifecycleSummary(c);
  const lifeSub = [
    device.warranty_expiry && fmtDate(device.warranty_expiry),
    device.support_expiry && `support to ${fmtDate(device.support_expiry)}`,
  ].filter(Boolean);
  const percent = c?.completeness.percent;
  const missing = c?.completeness.missing ?? [];
  const access = { ssh: "SSH", https_api: "HTTPS API", "": "" }[c?.access_method ?? ""];
  const recordTone: Tone = percent === 100 ? "ok" : "warn";

  return (
    <div className={styles.page} data-testid="device-record">
      <div className={styles.head}>
        <div className={styles.headMain}>
          <nav aria-label="Breadcrumb" className={styles.crumbs}>
            <button type="button" className={styles.crumbLink} onClick={onBack}>
              Devices
            </button>
            <span aria-hidden="true">/</span>
            <span aria-current="page">{device.name}</span>
          </nav>
          <div className={styles.titleRow}>
            <h2 className={styles.name}>{device.name}</h2>
            <Tag>{typeLabel(device.device_type ?? "")}</Tag>
            {device.role && <Tag>{device.role}</Tag>}
            <Tag tone={statusTone(device.status)}>{device.status}</Tag>
          </div>
          <span className={styles.place}>
            {[hardware, place.join(", ")].filter(Boolean).join(" · ")}
            {device.mgmt_ip && (
              <>
                {hardware || place.length ? " · " : ""}
                <Mono>{device.mgmt_ip}</Mono>
                {networkLabel && ` (${networkLabel})`}
              </>
            )}
          </span>
        </div>
        {/* A new conversation with the device named in its first message: the link carries the id, the chat looks the name up. */}
        <Link to={`/chat?device=${encodeURIComponent(device.id)}`}>Ask about this device</Link>
        {canManage && (
          <div className={styles.actions}>
            <Button disabled={facts.busy} onClick={() => void facts.refresh()}>
              Read from device
            </Button>
            <Button variant="primary" onClick={onEdit}>
              Edit
            </Button>
            {device.status !== "decommissioned" && (
              <div
                className={styles.menuWrap}
                onKeyDown={(e) => e.key === "Escape" && setMenuOpen(false)}
              >
                <Button aria-label="More actions" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>
                  ···
                </Button>
                {menuOpen && (
                  <div role="menu" className={styles.menu}>
                    <button type="button" role="menuitem" className={styles.menuItem} disabled={busy} onClick={() => void decommission()}>
                      Decommission
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <div className={styles.tiles}>
        <StatTile label="Lifecycle" value={life.text} tone={life.tone} sub={lifeSub.join(" · ") || undefined} />
        <StatTile
          label="Record"
          value={percent == null ? "—" : `${percent}% complete`}
          tone={percent == null ? "none" : recordTone}
          sub={percent == null ? undefined : missing.length ? `Missing ${missing.map((f) => fieldLabel(f).toLowerCase()).join(", ")}` : "All required fields are filled"}
        />
        <StatTile
          label="Monitoring"
          value={c?.monitored ? "Monitored" : "Not monitored"}
          sub={device.last_seen_at ? <>Last seen <DateTime value={device.last_seen_at} mode="relative" /></> : "Never seen by a source"}
        />
        <StatTile
          label="Last read from device"
          value={device.facts_at ? <DateTime value={device.facts_at} mode="relative" /> : "Never"}
          sub={device.facts_at ? "Values read from the device are marked device" : "Not read yet"}
        />
      </div>

      <div className={styles.subBar}>
        <div role="tablist" aria-label="Record sections" className={styles.subTabs}>
          <button type="button" role="tab" aria-selected={sub === "overview"} className={`${styles.subTab} ${sub === "overview" ? styles.subTabOn : ""}`} onClick={() => setSub("overview")}>
            Overview
          </button>
          <button type="button" role="tab" aria-selected={sub === "ports"} className={`${styles.subTab} ${sub === "ports" ? styles.subTabOn : ""}`} onClick={() => setSub("ports")}>
            Ports{portsData?.summary?.total ? <> <span className={styles.soon}>{portsData.summary.total}</span></> : null}
          </button>
          <button type="button" role="tab" aria-selected={sub === "backups"} className={`${styles.subTab} ${sub === "backups" ? styles.subTabOn : ""}`} onClick={() => setSub("backups")}>
            Backups
          </button>
          <button type="button" role="tab" aria-selected={sub === "changes"} className={`${styles.subTab} ${sub === "changes" ? styles.subTabOn : ""}`} onClick={() => setSub("changes")}>
            Changes <span className={styles.soon}>{proposals.length}</span>
          </button>
        </div>
        <span className={styles.legend}>
          Values are typed by a person unless marked <code>device</code> or <code>solarwinds</code>
        </span>
      </div>

      {sub === "ports" ? (
        ports.state.status === "loading" ? (
          <CenteredMessage title="Loading…" />
        ) : portsData ? (
          <DevicePorts deviceId={device.id} data={portsData} canManage={canManage} onChanged={ports.reload} onOpenDevice={onOpenDevice} />
        ) : (
          <CenteredMessage
            title="Could not load ports"
            detail={ports.state.status === "error" ? ports.state.error.message : ports.state.data.error}
            action={<Button onClick={ports.reload}>Retry</Button>}
          />
        )
      ) : sub === "backups" ? (
        <DeviceBackups deviceId={device.id} deviceName={device.name} canManage={canManage} onChanged={reload} />
      ) : sub === "changes" ? (
        <Section title="Changes">
          {proposals.length === 0 ? <p>No proposals for this device.</p> : <ChangeRows proposals={proposals} />}
        </Section>
      ) : (
        <>
          <div className={styles.grid}>
            <Section title="Identity">
              <KV
                rows={[
                  row("Name", mono(device.name), "name"),
                  row("Asset tag", device.asset_tag, "asset_tag"),
                  row("Type", typeLabel(device.device_type ?? ""), "device_type"),
                  row("Role", device.role, "role"),
                  row("Tags", device.tags.length ? device.tags.map((t) => <Tag key={t}>{t}</Tag>) : "", "tags"),
                ]}
              />
            </Section>
            <Section title="Hardware">
              <KV
                rows={[
                  row("Vendor", device.vendor, "vendor"),
                  row("Model", device.model, "model"),
                  row("Part number", device.part_number, "part_number"),
                  row("Serial", mono(device.serial), "serial"),
                  row("Base MAC", mono(device.base_mac), "base_mac"),
                ]}
              />
            </Section>
            <Section title="Network and software">
              <KV
                rows={[
                  row("Management IP", mono(device.mgmt_ip), "mgmt_ip"),
                  row("Platform", device.platform, "platform"),
                  row("Software", device.software_version, "software_version"),
                  row("HA group", device.ha_group, "ha_group"),
                  { label: "Reached by", value: access, source: access ? "computed" : "" },
                  { label: "Subnet", value: mono(portsData?.summary?.mgmt_subnet), source: portsData?.summary?.mgmt_subnet ? "device" : "" },
                  {
                    label: "Uplink",
                    value: uplink ? `${uplink.port} to ${uplink.neighbor}${uplink.neighbor_port ? ` ${uplink.neighbor_port}` : ""}` : "",
                    source: uplink ? "device" : "",
                  },
                ]}
              />
            </Section>
            <Section
              title="Place"
              action={device.place_id ? <Link to={{ search: `?tab=places&place=${device.place_id}` }}>Open in Places</Link> : undefined}
            >
              <KV
                rows={[
                  row("Place", device.place_id ? <PlacePath id={device.place_id} places={places} /> : "", "place_id"),
                  ...(!device.place_id && typeof device.custom?.old_place_text === "string"
                    ? [{ label: "Old place text", value: <Tag>{device.custom.old_place_text}</Tag>, source: "" }]
                    : []),
                  row("Unit", [device.rack_unit != null && `U${device.rack_unit}`, device.u_height != null && `${device.u_height}U high`].filter(Boolean).join(", "), "rack_unit"),
                ]}
              />
              {device.place?.kind === "rack" && device.rack_unit != null && <RackStrip device={device} />}
            </Section>
            <Section title="Ownership">
              <KV
                rows={[
                  row("Department", device.department, "department"),
                  row("Custodian", device.custodian, "custodian"),
                  row("Support contact", device.support_contact, "support_contact"),
                  row("Support contract", device.support_contract, "support_contract"),
                ]}
              />
            </Section>
            <Section title="Recent changes" action={proposals.length > 4 ? <button type="button" className={styles.link} onClick={() => setSub("changes")}>All {proposals.length} changes</button> : undefined}>
              {proposals.length === 0 ? <p>No proposals for this device.</p> : <ChangeRows proposals={proposals.slice(0, 4)} />}
            </Section>
          </div>

          <div className={styles.grid}>
            {(device.sources?.length ?? 0) > 0 && (
              <Section title="Sources">
                <ul data-testid="device-sources" className={styles.sources}>
                  {device.sources!.map((s) => (
                    <li key={`${s.source}:${s.source_id}`}>
                      <Mono>{s.source}</Mono> · id {s.source_id || "—"} · last seen{" "}
                      {s.last_seen_at ? <DateTime value={s.last_seen_at} mode="relative" /> : "—"}
                      {s.stale && <> <Tag tone="warn">stale</Tag></>}
                    </li>
                  ))}
                </ul>
              </Section>
            )}
            <FactsSection device={device} result={facts.result} error={facts.error} />
            <HaSection device={device} onOpen={onOpenDevice} />
          </div>

          <Section title="Lifecycle">
            <Timeline device={device} />
          </Section>
        </>
      )}
    </div>
  );
}
