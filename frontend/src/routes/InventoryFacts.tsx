/** Device-record sections: the live facts read and HA group members. */

import { useState } from "react";

import { refreshFacts, type Device, type FactsDeviceResult } from "@/api/inventory";
import { refreshPorts } from "@/api/inventoryPorts";
import { DateTime, Mono, ScopeNote, useToast } from "@/components";
import { Section } from "./inventory/kit";

/** Reads one device through the executor (facts, then ports); the record's header button and FactsSection share it. */
export function useFactsRefresh(name: string, onRefreshed: () => void) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FactsDeviceResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      const res = await refreshFacts({ devices: [name] });
      if (!res.ok) throw new Error(res.error || "Read failed.");
      const mine = res.results?.find((r) => r.device === name) ?? res.results?.[0];
      if (!mine) throw new Error("No result returned for this device.");
      setResult(mine);
      if (mine.status === "ok") {
        // facts first, then ports; a ports failure must not hide the facts that did land
        try {
          const ports = await refreshPorts({ devices: [name] });
          const failed = ports.failed?.[0]?.error;
          if (!ports.ok || failed) toast.error(`Ports: ${ports.error || failed}`);
        } catch (caught) {
          toast.error(`Ports: ${caught instanceof Error ? caught.message : String(caught)}`);
        }
        onRefreshed();
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      setError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  return { busy, result, error, refresh };
}

export function FactsSection({
  device,
  result,
  error,
}: {
  device: Device;
  result: FactsDeviceResult | null;
  error: string | null;
}) {
  return (
    <Section title="Last read from device">
      <div data-testid="device-facts" style={{ display: "grid", gap: "var(--space-2)" }}>
        <span>
          Last facts{" "}
          {device.facts_at ? <DateTime value={device.facts_at} mode="relative" /> : "never collected"}
        </span>
        {error && (
          <ScopeNote tone="crit">
            <div data-testid="facts-error">{error}</div>
          </ScopeNote>
        )}
        {result && (
          <div data-testid="facts-result" style={{ display: "grid", gap: "var(--space-1)" }}>
            {result.status !== "ok" && (
              <ScopeNote tone={result.status === "error" ? "crit" : "warn"}>
                <div>
                  {result.status === "skipped" ? "Skipped" : "Error"}: {result.error || "no detail"}
                </div>
              </ScopeNote>
            )}
            <div>
              Fields seen:{" "}
              {result.fields_seen.length ? (
                result.fields_seen.map((f) => <Mono key={f}>{f} </Mono>)
              ) : (
                "none"
              )}
            </div>
            <div>
              {result.proposals} proposals · {result.conflicts} conflicts · {result.unchanged}{" "}
              unchanged · {result.pinned} pinned
            </div>
            {result.warnings.length > 0 && (
              <ScopeNote tone="warn">
                <div data-testid="facts-warnings">{result.warnings.join("; ")}</div>
              </ScopeNote>
            )}
          </div>
        )}
      </div>
    </Section>
  );
}

export function HaSection({
  device,
  onOpen,
}: {
  device: Device;
  onOpen: (id: string) => void;
}) {
  const members = device.ha_members ?? [];
  if (!device.ha_group && members.length === 0) return null;
  return (
    <Section title="HA group">
      <div data-testid="device-ha">
        <p style={{ margin: 0 }}>
          <Mono>{device.ha_group || "—"}</Mono>
        </p>
        {members.length === 0 ? (
          <p>No other devices share this group.</p>
        ) : (
          <ul>
            {members.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  style={{ background: "none", border: 0, padding: 0, color: "var(--accent)", cursor: "pointer", textDecoration: "underline", font: "inherit" }}
                  onClick={() => onOpen(m.id)}
                >
                  {m.name}
                </button>{" "}
                · {m.status}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}
