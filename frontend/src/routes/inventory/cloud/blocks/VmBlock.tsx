/** A virtual machine: size, system, power state, and each network card with its subnet and public address. */

import type { ResourcePage } from "@/api/azure";
import { Mono, Table } from "@/components";

import { KV, Section } from "../../kit";
import { TagChips } from "../AzureResourceList";
import styles from "../cloud.module.css";
import { LinkName, fmt, linkOf, useLinkedPages } from "./GenericProps";

type NicProps = { ip_configs?: { name: string; private_ip: string; primary?: boolean }[] };

export function VmBlock({ page, onOpen }: { page: ResourcePage; onOpen: (id: string) => void }) {
  const r = page.resource;
  const p = r.props as { size?: string; os_type?: string; image?: Record<string, string>; zones?: string[] };
  const nics = page.links_out.filter((l) => l.kind === "nic");
  const cards = useLinkedPages(nics.map((n) => n.id));
  const pips = useLinkedPages(nics.map((n) => (n.id ? linkOf(cards.get(n.id)?.links_out, "public_ip")?.id : null)));
  const ipOf = (id: string | null) => (id ? ((pips.get(id)?.resource.props as { ip?: string } | undefined)?.ip ?? "") : "");
  const image = p.image ? [p.image.publisher, p.image.offer, p.image.sku].filter(Boolean).join(" ") : "";

  return (
    <>
      <Section title="Summary">
        <KV
          rows={[
            { label: "State", value: r.state_detail || r.state },
            { label: "Size", value: p.size ?? "" },
            { label: "System", value: [p.os_type, image].filter(Boolean).join(" · ") },
            { label: "Zones", value: p.zones?.length ? fmt(p.zones) : "" },
            { label: "Tags", value: <TagChips tags={r.tags} max={8} /> },
          ]}
        />
      </Section>
      <Section title="Network cards">
        {nics.length === 0 ? (
          <span className={styles.unset}>No network card is stored for this machine.</span>
        ) : (
          <Table>
            <thead>
              <tr><th>Network card</th><th>Private address</th><th>Subnet</th><th>Public address</th><th>Security group</th></tr>
            </thead>
            <tbody>
              {nics.map((n) => {
                const card = n.id ? cards.get(n.id) : undefined;
                const subnet = linkOf(card?.links_out, "subnet");
                const pip = linkOf(card?.links_out, "public_ip");
                const nsg = linkOf(card?.links_out, "nsg");
                const ips = (card?.resource.props as NicProps | undefined)?.ip_configs ?? [];
                return (
                  <tr key={n.to_native_id}>
                    <td><LinkName link={n} onOpen={onOpen} /></td>
                    <td><Mono>{ips.map((c) => c.private_ip).join(", ") || "—"}</Mono></td>
                    <td>{subnet ? <LinkName link={subnet} onOpen={onOpen} /> : <span className={styles.unset}>none</span>}</td>
                    <td>{pip ? <><LinkName link={pip} onOpen={onOpen} />{ipOf(pip.id) && <> <Mono>{ipOf(pip.id)}</Mono></>}</> : <span className={styles.unset}>none</span>}</td>
                    <td>{nsg ? <LinkName link={nsg} onOpen={onOpen} /> : <span className={styles.unset}>none</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Section>
    </>
  );
}
