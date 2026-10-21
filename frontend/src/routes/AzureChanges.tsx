/**
 * The Azure changes under the Changes tab: every change the assistant drafted, with the CR or SR number it was approved
 * with, and a filter on that number (one number may cover several changes). Read only; approval happens on the card in
 * the chat, where the number and the document are asked for. Hidden when the change plugin is off.
 */

import { useState } from "react";

import { ApiError } from "@/api/client";
import { getAzureChanges, type AzureChangeSummary } from "@/api/azureChange";
import { Button, Card, DateTime, Mono, Table, Tag, TextInput } from "@/components";
import { useAsync } from "@/lib/useAsync";

/** Which document stage a change has reached: the one for management, whether it was fetched, the one with the result. */
export function documentStatus(change: AzureChangeSummary): string {
  const docs = change.documents ?? [];
  if (docs.some((d) => d.version === "applied")) return "applied";
  const issued = docs.filter((d) => d.version === "issued");
  if (issued.length === 0) return change.kind === "azure_suggestion" ? "runbook" : "none";
  return issued.some((d) => d.downloaded) ? "issued, downloaded" : "issued";
}

export function AzureChanges() {
  const [filter, setFilter] = useState("");
  const [applied, setApplied] = useState("");
  const { state, reload } = useAsync(() => getAzureChanges(applied), [applied]);

  if (state.status === "loading") return null;
  if (state.status === "error") {
    // The plugin is off: there is nothing to list, and saying so on every visit is noise.
    if (state.error instanceof ApiError && state.error.status === 404) return null;
    return (
      <Card title="Azure changes">
        <p>{state.error.message}</p>
        <Button onClick={reload}>Retry</Button>
      </Card>
    );
  }
  // The server filters; this keeps the screen honest if it did not.
  const needle = applied.trim().toLowerCase();
  const changes = state.data.filter((c) => !needle || c.reference.toLowerCase().includes(needle));

  return (
    <Card
      title="Azure changes"
      description="The number an engineer approved each change with. One number may cover several changes."
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(filter);
        }}
        style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}
      >
        <TextInput
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter by CR or SR number"
          aria-label="Filter by CR or SR number"
          autoComplete="off"
        />
        <Button type="submit">Filter</Button>
        {applied && (
          <Button
            type="button"
            onClick={() => {
              setFilter("");
              setApplied("");
            }}
          >
            Clear
          </Button>
        )}
      </form>
      {changes.length ? (
        <Table>
          <thead>
            <tr>
              <th>Raised</th>
              <th>Change</th>
              <th>Where</th>
              <th>Status</th>
              <th>CR / SR number</th>
              <th>Requested by</th>
              <th>Approved by</th>
              <th>Document</th>
            </tr>
          </thead>
          <tbody>
            {changes.map((change) => (
              <tr key={change.id}>
                <td><DateTime value={change.created_at} /></td>
                <td>
                  <Mono>{change.playbook_name}</Mono>
                  <br />
                  {change.summary}
                </td>
                <td>{change.limit}</td>
                <td>{change.status.replace(/_/g, " ")}</td>
                <td>{change.reference ? <Mono>{change.reference}</Mono> : "—"}</td>
                <td>{change.requested_by || "—"}</td>
                <td>{change.approved_by || "—"}</td>
                <td>
                  <Tag tone={documentStatus(change) === "none" ? "warn" : "muted"}>{documentStatus(change)}</Tag>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <p>{applied ? "No Azure change has that number." : "No Azure change has been drafted."}</p>
      )}
    </Card>
  );
}
