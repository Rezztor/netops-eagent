/** An app service: its state, plan, host names and public access, and where it is joined to the network. */

import type { ResourcePage } from "@/api/azure";
import { Mono, Tag } from "@/components";

import { KV, Section } from "../../kit";
import { TagChips } from "../AzureResourceList";
import styles from "../cloud.module.css";
import { LinkName, fmt, linkOf, plural, useLinkedPages } from "./GenericProps";

export function AppServiceBlock({ page, onOpen }: { page: ResourcePage; onOpen: (id: string) => void }) {
  const r = page.resource;
  const p = r.props as {
    host_names?: string[];
    https_only?: boolean;
    public_network_access?: string;
    outbound_ips?: string[];
    runtime?: string;
  };
  const plan = linkOf(page.links_out, "plan");
  const subnet = linkOf(page.links_out, "vnet_integration");
  const first = useLinkedPages([plan?.id, subnet?.id]);
  const subnetLinks = subnet?.id ? first.get(subnet.id)?.links_out : undefined;
  const rt = linkOf(subnetLinks, "route_table");
  const nsg = linkOf(subnetLinks, "nsg");
  const second = useLinkedPages([rt?.id]);

  const planPage = plan?.id ? first.get(plan.id) : undefined;
  const workers = (planPage?.resource.props as { workers?: number } | undefined)?.workers;
  const planSku = (planPage?.resource.sku as { name?: string } | undefined)?.name;
  const routes = (rt?.id ? second.get(rt.id)?.resource.props : undefined) as { routes?: unknown[] } | undefined;
  const open = (p.public_network_access ?? "").toLowerCase() === "enabled";

  return (
    <>
      <Section title="Summary">
        <KV
          rows={[
            { label: "State", value: r.state_detail || r.state },
            {
              label: "Plan",
              value: plan ? (
                <>
                  <LinkName link={plan} onOpen={onOpen} />
                  {planSku && ` · ${planSku}`}
                  {workers != null && ` · ${plural(workers, "instance")}`}
                </>
              ) : (
                ""
              ),
            },
            { label: "Runtime", value: p.runtime ?? "" },
            { label: "Host names", value: p.host_names?.length ? <Mono>{p.host_names.join(", ")}</Mono> : "" },
            { label: "HTTPS only", value: p.https_only == null ? "" : fmt(p.https_only) },
            {
              label: "Public access",
              value: p.public_network_access ? (
                <>
                  {p.public_network_access} {open && <Tag tone="warn">open to the internet</Tag>}
                </>
              ) : (
                ""
              ),
            },
            { label: "Tags", value: <TagChips tags={r.tags} max={8} /> },
          ]}
        />
      </Section>
      <Section title="Network">
        <KV
          rows={[
            {
              label: "Joined to",
              value: subnet ? (
                <>
                  <LinkName link={subnet} onOpen={onOpen} />
                  {first.get(subnet.id ?? "")?.resource.parent && ` in ${first.get(subnet.id ?? "")!.resource.parent!.name}`}
                </>
              ) : (
                <span className={styles.unset}>not joined to a virtual network</span>
              ),
            },
            { label: "Route table there", value: subnet ? (rt ? <><LinkName link={rt} onOpen={onOpen} />{routes?.routes ? ` · ${plural(routes.routes.length, "route")}` : ""}</> : "not configured") : "" },
            { label: "Security group there", value: subnet ? (nsg ? <LinkName link={nsg} onOpen={onOpen} /> : "none") : "" },
            { label: "Outgoing addresses", value: p.outbound_ips?.length ? <Mono>{p.outbound_ips.join(", ")}</Mono> : "" },
          ]}
        />
      </Section>
    </>
  );
}
