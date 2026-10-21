/**
 * What the tenant paid: last month and month to date by service, from Azure Cost Management through the same
 * answer the chat gives. Every figure comes with its period, its currency, how late the data can be, and whether the
 * subscriptions it covers are all of them. Nothing here adds up, converts or estimates: it shows what came back.
 */

import { getAzureCost, type CostAnswer } from "@/api/azure";
import { DateTime, Table } from "@/components";
import { useAsync } from "@/lib/useAsync";

import { Notice, Section } from "../kit";
import styles from "./cloud.module.css";

const money = (row?: { cost: number; currency: string }) => (row ? `${row.cost.toFixed(2)} ${row.currency}` : "–");

function Answer({ title, state }: { title: string; state: ReturnType<typeof useAsync<CostAnswer>>["state"] }) {
  if (state.status === "loading") return <span className={styles.unset}>Loading {title.toLowerCase()}…</span>;
  const answer = state.status === "ready" ? state.data : null;
  if (!answer?.ok) return <Notice>{`Cost for ${title.toLowerCase()} is not available: ${answer?.error ?? (state.status === "error" ? state.error.message : "")}`}</Notice>;
  return answer.scope.complete ? null : (
    <Notice tone="warn">
      {title} covers {answer.scope.covers} only. Not included: {answer.scope.not_included.map((n) => `${n.subscription} (${n.reason})`).join("; ") || "some rows Azure did not return"}.
    </Notice>
  );
}

export function AzureCost() {
  const last = useAsync(() => getAzureCost({ question: "spend", timeframe: "last_month", group_by: "service" }), []);
  const month = useAsync(() => getAzureCost({ question: "spend", timeframe: "month_to_date", group_by: "service" }), []);
  const a = last.state.status === "ready" && last.state.data.ok ? last.state.data : null;
  const b = month.state.status === "ready" && month.state.data.ok ? month.state.data : null;
  const names = [...new Set([...(a?.rows ?? []), ...(b?.rows ?? [])].map((r) => r.name))];
  const cell = (answer: CostAnswer | null, name: string) => money(answer?.rows.find((r) => r.name === name));
  const fetched = [a?.fetched_at, b?.fetched_at].filter((x): x is string => Boolean(x)).sort()[0];

  return (
    <Section title="Cost">
      <Answer title="Last month" state={last.state} />
      <Answer title="Month to date" state={month.state} />
      {(a || b) && (
        <>
          <Table>
            <thead>
              <tr>
                <th>Service</th>
                <th>{a ? `Last month (${a.period.from} to ${a.period.to} ${a.period.timezone})` : "Last month"}</th>
                <th>{b ? `Month to date (${b.period.from} to ${b.period.to} ${b.period.timezone})` : "Month to date"}</th>
              </tr>
            </thead>
            <tbody>
              {names.map((name) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{cell(a, name)}</td>
                  <td>{cell(b, name)}</td>
                </tr>
              ))}
              <tr>
                <td><strong>Total</strong></td>
                <td><strong>{a?.totals.map(money).join(", ") || "–"}</strong></td>
                <td><strong>{b?.totals.map(money).join(", ") || "–"}</strong></td>
              </tr>
            </tbody>
          </Table>
          <p className={styles.sub}>
            Actual cost before tax{fetched && <> · fetched <DateTime value={fetched} /></>} (kept up to four hours). {(a ?? b)!.notes.slice(0, -1).join(" ")}
            {(a?.rows_not_shown || b?.rows_not_shown) ? ` The smaller services are not listed.` : ""}
          </p>
        </>
      )}
    </Section>
  );
}
