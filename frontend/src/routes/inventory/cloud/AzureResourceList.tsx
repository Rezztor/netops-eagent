/**
 * The list of resources: search, the four filters, "show removed", the table and its pages.
 * Read-only: a row opens the resource page and nothing here edits anything.
 */

import { getAzureWaste, listAzureResources, type CloudResource, type Facet, type ResourceFilters, type WasteFinding } from "@/api/azure";
import { CenteredMessage, DateTime, EmptyRow, Pagination, SearchInput, Select, Table, Tag, type TagTone } from "@/components";
import { useAsync } from "@/lib/useAsync";

import styles from "./cloud.module.css";

export const PAGE = 50;

const STATE_TONE: Record<string, TagTone> = { running: "ok", stopped: "warn", failed: "crit" };

export function StateTag({ resource }: { resource: Pick<CloudResource, "state" | "state_detail"> }) {
  return (
    <span title={resource.state_detail || undefined}>
      <Tag tone={STATE_TONE[resource.state] ?? "muted"}>{resource.state}</Tag>
    </span>
  );
}

export function TagChips({ tags, max = 3 }: { tags: Record<string, string>; max?: number }) {
  const entries = Object.entries(tags ?? {});
  if (!entries.length) return <span className={styles.unset}>no tags</span>;
  return (
    <span className={styles.chips}>
      {entries.slice(0, max).map(([name, value]) => (
        <span key={name} className={styles.chip}>{value ? `${name}=${value}` : name}</span>
      ))}
      {entries.length > max && <span className={styles.chip}>+{entries.length - max}</span>}
    </span>
  );
}

const facet = (f: Facet) => (typeof f === "string" ? { value: f, count: undefined } : { value: f.value ?? f.name ?? "", count: f.count });

/** What the last pull did to a row, in one word. */
function lastPull(r: CloudResource, since: number): string {
  if (r.removed_at) return "removed";
  if (new Date(r.first_seen_at).getTime() >= since) return "new";
  if (r.changed_at && new Date(r.changed_at).getTime() >= since) return "changed";
  return "";
}

export interface ListQuery {
  q: string;
  type: string;
  region: string;
  state: string;
  tag: string;
  /** `tag:<name>`: only resources that lack that tag. */
  without: string;
  removed: boolean;
}

export function AzureResourceList({
  scope,
  query,
  page,
  since,
  onChange,
  onPage,
  onOpen,
}: {
  /** What the tree and the tiles chose: subscription, resource_group, category, attention. */
  scope: ResourceFilters;
  query: ListQuery;
  page: number;
  /** When the pull the stored data is "as of" started, for the "Last pull" column. */
  since: number;
  onChange: (patch: Partial<Record<keyof ListQuery, string>>) => void;
  onPage: (page: number) => void;
  onOpen: (id: string) => void;
}) {
  const params: ResourceFilters = {
    ...scope,
    q: query.q || undefined,
    type: query.type || undefined,
    region: query.region || undefined,
    state: query.state || undefined,
    tag: query.tag || undefined,
    without: query.without || undefined,
    include_removed: query.removed || undefined,
    limit: PAGE,
    offset: page * PAGE,
  };
  const { state } = useAsync(() => listAzureResources(params), [params]);
  const waste = scope.attention === "waste";
  const { state: found } = useAsync(
    () => (waste ? getAzureWaste({ subscription: scope.subscription, resource_group: scope.resource_group }) : Promise.resolve(null)),
    [waste, scope.subscription, scope.resource_group],
  );
  const findings = new Map<string, WasteFinding[]>();
  if (found.status === "ready" && found.data?.ok) {
    for (const f of found.data.findings) findings.set(f.id, [...(findings.get(f.id) ?? []), f]);
  }

  if (state.status === "error") return <CenteredMessage title="Could not load resources" detail={state.error.message} />;
  const data = state.status === "ready" && state.data.ok ? state.data : null;
  if (state.status === "ready" && !data) return <CenteredMessage title="Could not load resources" detail={state.data.error} />;

  const options = (current: string, values: Facet[]) => {
    const found = values.map(facet).filter((f) => f.value);
    return current && !found.some((f) => f.value === current) ? [...found, { value: current, count: undefined }] : found;
  };
  const pick = (label: string, key: "type" | "region" | "state" | "tag" | "without", values: { value: string; text?: string }[]) => (
    <Select aria-label={label} value={query[key]} onChange={(e) => onChange({ [key]: e.target.value })}>
      <option value="">{`${label}: any`}</option>
      {values.map((v) => (
        <option key={v.value} value={v.value}>{v.text ?? v.value}</option>
      ))}
    </Select>
  );
  const types = data?.facets.types ?? [];

  return (
    <div className={styles.stack}>
      <div className={styles.toolbar}>
        <div className={styles.toolbarSearch}>
          <SearchInput value={query.q} onChange={(q) => onChange({ q })} placeholder="Search name, address, tag…" label="Search resources" />
        </div>
        {pick("Type", "type", [
          ...types.map((t) => ({ value: t.type, text: `${t.label} (${t.count})` })),
          ...(query.type && !types.some((t) => t.type === query.type) ? [{ value: query.type }] : []),
        ])}
        {pick("Region", "region", options(query.region, data?.facets.regions ?? []))}
        {pick("State", "state", options(query.state, data?.facets.states ?? []))}
        {pick("Tag", "tag", options(query.tag, data?.facets.tag_names ?? []))}
        {pick("Missing tag", "without", [
          ...options("", data?.facets.tag_names ?? []).map((f) => ({ value: `tag:${f.value}`, text: f.value })),
          ...(query.without && !(data?.facets.tag_names ?? []).map(facet).some((f) => `tag:${f.value}` === query.without) ? [{ value: query.without }] : []),
        ])}
        <label className={styles.check}>
          <input type="checkbox" checked={query.removed} onChange={(e) => onChange({ removed: e.target.checked ? "1" : "" })} />
          Show removed
        </label>
      </div>
      {found.status === "ready" && found.data?.ok && (
        <p className={styles.asOf}>
          {found.data.findings.length} finding{found.data.findings.length === 1 ? "" : "s"}
          {found.data.priced > 0 && (
            <>
              {" "}· about {found.data.total_usd_month.toFixed(2)} USD a month at list price for the {found.data.priced} that have one
            </>
          )}
          {found.data.priced_at && (
            <>
              {" "}(price list of <DateTime value={found.data.priced_at} mode="date" />, 730 hours a month, before data transfer, usage and discounts)
            </>
          )}
          . Each is a pointer to look, not a verdict.
        </p>
      )}
      {found.status === "ready" && found.data && !found.data.ok && <CenteredMessage title="Could not load the findings" detail={found.data.error} />}
      <Table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Resource group</th>
            <th>Region</th>
            <th>State</th>
            <th>Tags</th>
            <th>Last pull</th>
            {waste && <th>Why</th>}
          </tr>
        </thead>
        <tbody>
          {data?.resources.length === 0 && <EmptyRow colSpan={waste ? 8 : 7}>No resources match.</EmptyRow>}
          {data?.resources.map((r) => (
            <tr key={r.id} className={r.removed_at ? styles.removed : undefined} data-removed={r.removed_at ? "true" : undefined}>
              <td>
                <button type="button" className={`${styles.link} ${styles.name}`} onClick={() => onOpen(r.id)}>{r.name}</button>
              </td>
              <td>{r.label}</td>
              <td>{r.resource_group.name}</td>
              <td>{r.region}</td>
              <td><StateTag resource={r} /></td>
              <td><TagChips tags={r.tags} /></td>
              <td className={styles.dim}>{lastPull(r, since)}</td>
              {waste && (
                <td>
                  {(findings.get(r.id) ?? []).map((f) => (
                    <div key={f.rule}>
                      <strong>{f.title}</strong>
                      {f.usd_month != null && <span className={styles.sub}> · {f.usd_month.toFixed(2)} USD a month (list)</span>}
                      {f.cost_last_month && (
                        <span className={styles.sub} title={f.cost_last_month.period}>
                          {" "}· cost last month {f.cost_last_month.cost.toFixed(2)} {f.cost_last_month.currency} (actual, credits not deducted)
                        </span>
                      )}
                      <div className={styles.sub}>{f.evidence}</div>
                    </div>
                  ))}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </Table>
      {data && (
        <Pagination page={page} pageCount={Math.max(1, Math.ceil(data.total / PAGE))} onPage={onPage} total={data.total} matched={data.total} noun="resources" />
      )}
    </div>
  );
}
