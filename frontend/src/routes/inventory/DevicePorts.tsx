/** The Ports tab: what the last read saw on the device's ports and parts. Observed data, never typed. */

import { useState } from "react";

import { markUplink, type PortRow, type PortsResponse } from "@/api/inventoryPorts";
import { Button, DateTime, Mono, Table, Tag, useToast } from "@/components";
import { Notice, Section, StatTile } from "./kit";
import styles from "./DevicePorts.module.css";

const stateOf = (p: PortRow) =>
  p.oper_up == null ? { text: "unknown", cls: styles.off } : p.oper_up ? { text: "up", cls: styles.up } : { text: p.admin_up === false ? "disabled" : "down", cls: styles.down };

/** "1, 10-12, 20": a VLAN list short enough for a table cell. */
export function vlanRanges(vlans: number[]): string {
  const sorted = [...new Set(vlans)].sort((a, b) => a - b);
  const out: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (sorted[j + 1] === sorted[j]! + 1) j++;
    out.push(j > i + 1 ? `${sorted[i]}-${sorted[j]}` : sorted.slice(i, j + 1).join(", "));
    i = j + 1;
  }
  return out.join(", ");
}

export function DevicePorts({
  deviceId,
  data,
  canManage,
  onChanged,
  onOpenDevice,
}: {
  deviceId: string;
  data: PortsResponse;
  canManage: boolean;
  onChanged: () => void;
  onOpenDevice: (id: string) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState("");
  const ports = data.interfaces ?? [];
  const parts = data.components ?? [];
  const s = data.summary;

  if (!data.observed_at && ports.length === 0 && parts.length === 0) {
    return (
      <Notice>
        Ports have not been read from this device yet.{canManage ? " Use Read from device." : ""}
      </Notice>
    );
  }

  async function toggle(p: PortRow) {
    setBusy(p.name);
    try {
      const res = await markUplink({ device_id: deviceId, name: p.name, is_uplink: !p.is_uplink });
      if (!res.ok) throw new Error(res.error || "Could not save.");
      onChanged();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  return (
    <div className={styles.root} data-testid="device-ports">
      {s && (
        <div className={styles.tiles}>
          <StatTile label="Ports up" value={`${s.up} of ${s.total}`} tone={s.total && s.up === s.total ? "ok" : "none"} sub={`${s.down} down${s.unknown ? `, ${s.unknown} unknown` : ""}`} />
          <StatTile label="PoE" value={s.poe ? `${s.poe.watts} W` : "—"} sub={s.poe ? `${s.poe.ports} powered port${s.poe.ports === 1 ? "" : "s"}` : "No PoE reported"} />
          <StatTile label="Optics" value={s.optics.reduce((n, o) => n + o.count, 0)} sub={s.optics.map((o) => `${o.count} ${o.media}`).join(", ") || "None reported"} />
          <StatTile label="VLANs" value={s.vlans.length} sub={vlanRanges(s.vlans) || "None reported"} />
        </div>
      )}
      <span className={styles.meta}>
        Last read {data.observed_at ? <DateTime value={data.observed_at} mode="relative" /> : "never"}. Read from the device; each read replaces the last.
      </span>

      <Section title={`Ports (${ports.length})`}>
        <Table>
          <thead>
            <tr>
              <th>Port</th>
              <th>State</th>
              <th>Speed</th>
              <th>Mode</th>
              <th>Description</th>
              <th>Neighbour</th>
              <th>Uplink</th>
            </tr>
          </thead>
          <tbody>
            {ports.map((p) => {
              const st = stateOf(p);
              return (
                <tr key={p.name} data-testid={`port-${p.name}`}>
                  <td><Mono>{p.name}</Mono>{p.alias && <span className={styles.meta}> {p.alias}</span>}</td>
                  <td className={st.cls}>{st.text}</td>
                  <td className={styles.num}>{p.speed || "—"}</td>
                  <td>{[p.mode, p.vlans.length ? vlanRanges(p.vlans) : ""].filter(Boolean).join(" ") || "—"}</td>
                  <td className={styles.desc}>{p.description || "—"}</td>
                  <td>
                    {p.neighbor ? (
                      <>
                        {p.neighbor.device_id ? (
                          <button type="button" className={styles.link} onClick={() => onOpenDevice(p.neighbor!.device_id!)}>
                            {p.neighbor.name}
                          </button>
                        ) : (
                          p.neighbor.name
                        )}
                        {p.neighbor.port && <span className={styles.meta}> {p.neighbor.port}</span>}
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    {p.uplink_source === "neighbour" ? (
                      <Tag tone="accent">uplink (neighbour)</Tag>
                    ) : canManage ? (
                      <Button aria-label={`${p.is_uplink ? "Unmark" : "Mark"} ${p.name} as uplink`} disabled={busy === p.name} onClick={() => void toggle(p)}>
                        {p.is_uplink ? "Unmark" : "Mark"}
                      </Button>
                    ) : p.is_uplink ? (
                      <Tag tone="accent">uplink</Tag>
                    ) : (
                      ""
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Section>

      <Section title={`Parts (${parts.length})`}>
        {parts.length === 0 ? (
          <p>No parts reported.</p>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Kind</th>
                <th>Name</th>
                <th>Part number</th>
                <th>Serial</th>
                <th>Description</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {parts.map((c) => (
                <tr key={`${c.kind}:${c.name}`}>
                  <td>{c.kind.replace("_", " ")}</td>
                  <td><Mono>{c.name}</Mono></td>
                  <td>{c.part_number ? <Mono>{c.part_number}</Mono> : "—"}</td>
                  <td>{c.serial ? <Mono>{c.serial}</Mono> : "—"}</td>
                  <td className={styles.desc}>{c.description || "—"}</td>
                  <td>{c.expires_at ? <DateTime value={c.expires_at} mode="date" /> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Section>
    </div>
  );
}
