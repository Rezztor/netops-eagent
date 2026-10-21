/**
 * One resource: summary (a purpose-made block for networks, apps and machines), what it links to and what links
 * to it, its last changes, and the stored fields. Read-only: the one outward link opens the same resource in Azure.
 */

import { CATEGORY_LABELS, getAzureResource, type CloudChange, type CloudLink, type CloudResource } from "@/api/azure";
import { Button, CenteredMessage, DateTime, Mono, Tag } from "@/components";
import { useAsync } from "@/lib/useAsync";

import { Notice, Section, KV, fieldLabel } from "../kit";
import { StateTag, TagChips } from "./AzureResourceList";
import { AzureSignals } from "./AzureSignals";
import { AppServiceBlock } from "./blocks/AppServiceBlock";
import { GenericProps, LinkName, fmt } from "./blocks/GenericProps";
import { VmBlock } from "./blocks/VmBlock";
import { VnetBlock } from "./blocks/VnetBlock";
import styles from "./cloud.module.css";

const EXPOSURE: Record<string, string> = {
  public_ip: "has a public address",
  open_rule: "a rule lets any source in",
  public_access: "public network access is on",
  public_blob: "blobs can be read without signing in",
  external_ingress: "reachable from the internet",
};

const RELATION: Record<string, string> = {
  route_table: "Route table",
  nsg: "Security group",
  nat_gateway: "NAT gateway",
  peering: "Peering",
  subnet: "Subnet",
  public_ip: "Public address",
  nic: "Network card",
  plan: "App Service plan",
  vnet_integration: "Joined to subnet",
  managed_by: "Managed by",
  ip_configuration: "Attached to",
  nat_gateway_ip: "NAT gateway",
  public_ip_prefix: "Address prefix",
  vnet_gateway: "Network gateway",
  local_gateway: "Local gateway",
  target: "Connects to",
  source: "Made from",
  firewall_policy: "Firewall policy",
  virtual_hub: "Virtual hub",
  protects: "Protects",
  vm: "Machine",
  elastic_pool: "Elastic pool",
};

const CHANGE_TONE = { added: "ok", changed: "accent", removed: "crit", returned: "warn" } as const;

const BLOCKS = {
  "microsoft.network/virtualnetworks": VnetBlock,
  "microsoft.web/sites": AppServiceBlock,
  "microsoft.compute/virtualmachines": VmBlock,
} as const;

/** "props.https_only" as "HTTPS only", "relations.route_table" as "Route table", "tags.env" as "Tag env". */
function fieldName(path: string): string {
  const [head, ...rest] = path.split(".");
  const name = rest.join(".");
  if (head === "relations") return RELATION[name] ?? fieldLabel(name);
  if (head === "tags") return `Tag ${name}`;
  if (head === "props" && name === "https_only") return "HTTPS only";
  return fieldLabel(name || head!);
}

export function ChangeLine({ change }: { change: CloudChange }) {
  const fields = Object.entries(change.fields ?? {});
  return (
    <div className={styles.change}>
      <span>
        <DateTime value={change.at} /> <Tag tone={CHANGE_TONE[change.change]}>{change.change}</Tag>
        {change.resource && <> {change.resource.name}</>}
      </span>
      {fields.length > 0 && (
        <ul className={styles.fields}>
          {fields.map(([path, v]) => (
            <li key={path}>{`${fieldName(path)}: ${fmt(v.old)} became ${fmt(v.new)}`}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Links({ title, links, onOpen }: { title: string; links: CloudLink[]; onOpen: (id: string) => void }) {
  if (!links.length) return null;
  return (
    <div className={styles.inner}>
      <h4 className={styles.innerTitle}>{title}</h4>
      {links.map((l) => (
        <div key={`${l.kind}${l.to_native_id}${l.id}`} className={styles.line}>
          <span>
            <span className={styles.dim}>{RELATION[l.kind] ?? l.kind}</span> <LinkName link={l} onOpen={onOpen} />
            {!l.id && <span className={styles.sub}> not readable by this account</span>}
          </span>
          {l.label && <span className={styles.dim}>{l.label}</span>}
        </div>
      ))}
    </div>
  );
}

export function AzureResourcePage({
  id,
  onBack,
  onOpen,
  onScope,
}: {
  id: string;
  onBack: () => void;
  onOpen: (id: string) => void;
  /** Breadcrumbs: a subscription or group (and a category) as the list's scope. */
  onScope: (node: string, cat?: string) => void;
}) {
  const { state } = useAsync(() => getAzureResource(id), [id]);
  if (state.status === "loading") return <CenteredMessage title="Loading…" />;
  const failure = state.status === "error" ? state.error.message : state.data.ok ? null : (state.data.error ?? "Not found.");
  if (failure) return <CenteredMessage title="Could not open the resource" detail={failure} action={<Button onClick={onBack}>Back</Button>} />;

  if (state.status !== "ready") return null;
  const page = state.data;
  const r: CloudResource = page.resource;
  const Block = (BLOCKS as Record<string, typeof VnetBlock | undefined>)[r.type];

  return (
    <div className={styles.stack}>
      <div className={styles.crumbs}>
        <Button variant="ghost" onClick={onBack}>← Back</Button>
        <button type="button" className={styles.link} onClick={() => onScope(r.subscription.id)}>{r.subscription.name}</button>›
        <button type="button" className={styles.link} onClick={() => onScope(r.resource_group.id)}>{r.resource_group.name}</button>›
        <button type="button" className={styles.link} onClick={() => onScope(r.resource_group.id, r.category)}>{CATEGORY_LABELS[r.category]}</button>
      </div>
      <div className={styles.titleRow}>
        <h2 className={styles.title}>{r.name}</h2>
        <StateTag resource={r} />
        {page.exposure.length > 0 && (
          <span title={page.exposure.map((x) => EXPOSURE[x] ?? x).join(", ")}>
            <Tag tone="warn">open to the internet</Tag>
          </span>
        )}
        <span className={styles.spacer} />
        {page.portal_url && (
          <a href={page.portal_url} target="_blank" rel="noreferrer noopener">Open in Azure portal</a>
        )}
      </div>
      <p className={styles.asOf}>
        {r.label} · {r.region || "no region"} · as of <DateTime value={r.last_seen_at} /> · first seen <DateTime value={r.first_seen_at} mode="date" />
      </p>
      {r.removed_at && (
        <Notice tone="warn">
          Azure no longer reports this resource (since <DateTime value={r.removed_at} />). It is kept for a while, greyed out.
        </Notice>
      )}
      {page.exposure.length > 0 && (
        <Notice tone="warn">Worth a look: {page.exposure.map((x) => EXPOSURE[x] ?? x).join("; ")}. This is a pointer, not a verdict.</Notice>
      )}
      <div className={styles.blocks}>
        {Block ? (
          <Block page={page} onOpen={onOpen} />
        ) : (
          <Section title="Summary">
            <KV
              rows={[
                { label: "State", value: r.state_detail || r.state },
                { label: "Size", value: (r.sku as { name?: string }).name ?? "" },
                { label: "Tags", value: <TagChips tags={r.tags} max={8} /> },
              ]}
            />
          </Section>
        )}
        {(page.links_out.length > 0 || page.links_in.length > 0) && (
          <Section title="Linked resources">
            <Links title="Uses" links={page.links_out} onOpen={onOpen} />
            <Links title="Used by" links={page.links_in} onOpen={onOpen} />
          </Section>
        )}
        <Section title="Changes">
          {page.changes.length === 0 ? (
            <span className={styles.unset}>Nothing has changed since it was first seen.</span>
          ) : (
            <div>{page.changes.map((c) => <ChangeLine key={c.id} change={c} />)}</div>
          )}
        </Section>
        {page.signals && <AzureSignals signals={page.signals} />}
        <Section title="Stored fields">
          {Block ? (
            <details className={styles.details}>
              <summary>All stored fields</summary>
              <GenericProps props={r.props} />
            </details>
          ) : (
            <GenericProps props={r.props} />
          )}
          <Notice>
            Only named fields are stored. App settings, connection strings, keys and secrets are never read. <Mono>{r.native_id}</Mono>
          </Notice>
        </Section>
      </div>
    </div>
  );
}
