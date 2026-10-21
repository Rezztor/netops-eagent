/** The stored fields of a resource as a table of label and value, for any type. Also the small helpers the blocks share. */

import { useEffect, useState } from "react";

import { getAzureResource, type CloudLink, type ResourcePage } from "@/api/azure";
import { Mono } from "@/components";

import { KV, fieldLabel } from "../../kit";
import styles from "../cloud.module.css";

const last = (id: string) => id.slice(id.lastIndexOf("/") + 1);

/** A stored value as a person reads it: Yes/No, an Azure ID as its last segment, "none" for nothing. */
export function fmt(value: unknown): string {
  if (value == null || value === "" || (Array.isArray(value) && value.length === 0)) return "none";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value.startsWith("/") ? last(value) : value;
  if (Array.isArray(value)) return value.map(fmt).join(", ");
  if (typeof value === "object") {
    const { id, ...rest } = value as Record<string, unknown>;
    const more = Object.entries(rest).map(([k, v]) => `${k}: ${fmt(v)}`).join(", ");
    return [id != null ? fmt(id) : "", more && `(${more})`].filter(Boolean).join(" ");
  }
  return String(value);
}

const isRows = (v: unknown): v is Record<string, unknown>[] =>
  Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === "object" && !Array.isArray(x));

/** A list of objects (rules, routes, node pools) as a small table. */
export function MiniTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  return (
    <table className={styles.mini}>
      <thead>
        <tr>{columns.map((c) => <th key={c}>{fieldLabel(c)}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{columns.map((c) => <td key={c}>{fmt(r[c])}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

export function GenericProps({ props }: { props: Record<string, unknown> }) {
  const entries = Object.entries(props ?? {});
  if (!entries.length) return <span className={styles.unset}>No fields are stored for this type.</span>;
  const simple = entries.filter(([, v]) => !isRows(v));
  return (
    <div className={styles.inner}>
      <KV rows={simple.map(([k, v]) => ({ label: fieldLabel(k), value: v === "" || v == null ? "" : <Mono>{fmt(v)}</Mono> }))} />
      {entries.filter(([, v]) => isRows(v)).map(([k, v]) => (
        <div key={k} className={styles.inner}>
          <h4 className={styles.innerTitle}>{fieldLabel(k)}</h4>
          <MiniTable rows={v as Record<string, unknown>[]} />
        </div>
      ))}
    </div>
  );
}

/** The pages of other resources a block needs (a route table's routes, a network card's subnet). Failures are left out. */
export function useLinkedPages(ids: (string | null | undefined)[]): Map<string, ResourcePage> {
  const wanted = [...new Set(ids.filter((i): i is string => Boolean(i)))];
  const key = wanted.join("|");
  const [pages, setPages] = useState(() => new Map<string, ResourcePage>());
  useEffect(() => {
    let cancelled = false;
    void Promise.all(wanted.map((id) => getAzureResource(id).catch(() => null))).then((all) => {
      if (!cancelled) setPages(new Map(all.flatMap((p, i) => (p?.ok ? [[wanted[i]!, p] as const] : []))));
    });
    return () => {
      cancelled = true;
    };
    // `key` is the list of ids.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return pages;
}

export const linkOf = (links: CloudLink[] | undefined, kind: string) => (links ?? []).find((l) => l.kind === kind);

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** A link to another resource, or its name alone when it is outside what the account can read. */
export function LinkName({ link, onOpen }: { link: Pick<CloudLink, "id" | "name">; onOpen: (id: string) => void }) {
  return link.id ? (
    <button type="button" className={styles.link} onClick={() => onOpen(link.id!)}>{link.name}</button>
  ) : (
    <span title="Outside what this account can read">{link.name}</span>
  );
}
