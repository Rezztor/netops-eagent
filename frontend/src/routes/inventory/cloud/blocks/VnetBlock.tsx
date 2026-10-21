/** A virtual network: its address space, each subnet with its route table and security group, the routes, the peerings. */

import type { ResourcePage } from "@/api/azure";
import { Mono, Table, Tag } from "@/components";

import { KV, Notice, Section } from "../../kit";
import styles from "../cloud.module.css";
import { LinkName, MiniTable, fmt, linkOf, useLinkedPages } from "./GenericProps";

type Peering = {
  name: string;
  state: string;
  remote_id: string;
  remote_address_space?: string[];
  allow_forwarded?: boolean;
  allow_gateway_transit?: boolean;
  use_remote_gateways?: boolean;
};

/** Azure's own subnets: having no route table is normal for them. */
const RESERVED = new Set(["gatewaysubnet", "azurefirewallsubnet", "azurefirewallmanagementsubnet", "azurebastionsubnet", "routeserversubnet"]);

export function VnetBlock({ page, onOpen }: { page: ResourcePage; onOpen: (id: string) => void }) {
  const props = page.resource.props as { address_space?: string[]; dns_servers?: string[]; peerings?: Peering[] };
  const subnets = page.children;
  const peerings = props.peerings ?? [];

  const tables = new Map<string, { id: string | null; name: string; subnets: string[] }>();
  for (const s of subnets) {
    const rt = linkOf(s.links_out, "route_table");
    if (!rt) continue;
    const entry = tables.get(rt.to_native_id) ?? { id: rt.id, name: rt.name, subnets: [] };
    entry.subnets.push(s.name);
    tables.set(rt.to_native_id, entry);
  }
  const loaded = useLinkedPages([...tables.values()].map((t) => t.id));
  const missing = subnets.filter((s) => !linkOf(s.links_out, "route_table") && !RESERVED.has(s.name.toLowerCase())).length;
  const down = peerings.filter((p) => p.state.toLowerCase() !== "connected").length;
  const remote = (p: Peering) => page.links_out.find((l) => l.kind === "peering" && l.to_native_id === p.remote_id.toLowerCase());

  return (
    <>
      <Section title="Summary">
        <KV
          rows={[
            { label: "Address space", value: <Mono>{fmt(props.address_space)}</Mono> },
            { label: "DNS servers", value: <Mono>{fmt(props.dns_servers)}</Mono> },
            { label: "Subnets", value: `${subnets.length}${missing ? ` · ${missing} with no route table` : ""}` },
            { label: "Peerings", value: `${peerings.length}${down ? ` · ${down} disconnected` : ""}` },
          ]}
        />
      </Section>
      <Section title="Subnets">
        <Table>
          <thead>
            <tr><th>Subnet</th><th>Range</th><th>Route table</th><th>Security group</th></tr>
          </thead>
          <tbody>
            {subnets.map((s) => {
              const rt = linkOf(s.links_out, "route_table");
              const nsg = linkOf(s.links_out, "nsg");
              return (
                <tr key={s.id}>
                  <td><button type="button" className={`${styles.link} ${styles.name}`} onClick={() => onOpen(s.id)}>{s.name}</button></td>
                  <td><Mono>{fmt((s.props as { address_prefixes?: string[] }).address_prefixes)}</Mono></td>
                  <td>{rt ? <LinkName link={rt} onOpen={onOpen} /> : <span className={styles.unset}>{RESERVED.has(s.name.toLowerCase()) ? "none" : "not configured"}</span>}</td>
                  <td>{nsg ? <LinkName link={nsg} onOpen={onOpen} /> : <span className={styles.unset}>none</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Section>
      {[...tables.entries()].map(([native, t]) => {
        const routes = (loaded.get(t.id ?? "")?.resource.props as { routes?: Record<string, unknown>[]; bgp_propagation?: boolean } | undefined);
        return (
          <Section key={native} title={`Routes in ${t.name} (on ${t.subnets.join(", ")})`}>
            {routes ? (
              <>
                {routes.routes?.length ? (
                  <MiniTable rows={routes.routes.map((r) => ({ route: r.name, address_range: r.prefix, next_hop: `${fmt(r.next_hop_type)}${r.next_hop_ip ? ` ${r.next_hop_ip}` : ""}` }))} />
                ) : (
                  <span className={styles.unset}>This route table has no routes.</span>
                )}
                <Notice>
                  Routes learned from the gateway: {routes.bgp_propagation === false ? "switched off" : "allowed"}. These are the routes you configured. The full list Azure applies on a network card needs a live read (later phase).
                </Notice>
              </>
            ) : (
              <span className={styles.dim}>Reading the routes…</span>
            )}
          </Section>
        );
      })}
      {peerings.length > 0 && (
        <Section title="Peerings">
          <Table>
            <thead>
              <tr><th>Peering</th><th>To</th><th>State</th><th>Flags</th></tr>
            </thead>
            <tbody>
              {peerings.map((p) => {
                const link = remote(p);
                const flags = [p.allow_forwarded && "forwarded traffic", p.allow_gateway_transit && "gateway transit", p.use_remote_gateways && "uses remote gateway"].filter(Boolean);
                return (
                  <tr key={p.name}>
                    <td>{p.name}</td>
                    <td>
                      {link ? <LinkName link={link} onOpen={onOpen} /> : <Mono>{p.remote_id.slice(p.remote_id.lastIndexOf("/") + 1)}</Mono>}
                      {!link?.id && <span className={styles.sub}> not readable by this account{p.remote_address_space?.length ? ` · ${p.remote_address_space.join(", ")}` : ""}</span>}
                    </td>
                    <td><Tag tone={p.state.toLowerCase() === "connected" ? "ok" : "warn"}>{p.state}</Tag></td>
                    <td className={styles.dim}>{flags.length ? flags.join(", ") : "none"}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </Section>
      )}
    </>
  );
}
