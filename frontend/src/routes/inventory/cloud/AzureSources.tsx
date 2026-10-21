/** Sources tab, Azure card: the last pull, the next one, the credential's expiry and Pull now. Reads only. */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { getAzureStatus, startAzurePull, type AzureRun, type AzureStatus } from "@/api/azure";
import { PERMISSIONS } from "@/api/types";
import { Button, Card, CenteredMessage, DateTime, Tag, useToast, type TagTone } from "@/components";
import { useCan } from "@/lib/session";

import { KV, Notice } from "../kit";
import { startedBy } from "./AzureHistory";
import styles from "./AzureSources.module.css";

const POLL_MS = 3000;
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function cardStatus(s: AzureStatus): { label: string; tone: TagTone } {
  if (!s.enabled) return { label: "Disabled", tone: "muted" };
  if (!s.configured) return { label: "Not set up", tone: "warn" };
  if (s.running) return { label: "Running", tone: "accent" };
  if (s.last_run?.status === "failed") return { label: "Last pull failed", tone: "crit" };
  if (s.last_run?.status === "partial") return { label: "Partial", tone: "warn" };
  return { label: "Ready", tone: "ok" };
}

function progress(run: AzureRun): string {
  const subs = run.counts?.subscriptions;
  if (!subs || !subs.listed) return "Signing in and listing subscriptions…";
  return `${subs.read + subs.failed + subs.skipped} of ${plural(subs.listed, "subscription")} done`;
}

function lastRunRows(s: AzureStatus, run: AzureRun) {
  const counts = run.counts;
  const subs = counts?.subscriptions;
  const unreadable = s.as_of.subscriptions.filter((x) => !x.readable);
  const failed = (counts?.per_subscription ?? []).filter((x) => x.status === "failed");
  return [
    {
      label: "Last pull",
      value: (
        <>
          <DateTime value={run.finished_at || run.started_at} />
          {counts?.duration_s != null && ` · took ${Math.round(counts.duration_s)} s`} · by {startedBy(run)}
        </>
      ),
    },
    {
      label: "Result",
      value: (
        <>
          {run.status === "failed"
            ? "Failed"
            : subs
              ? `${subs.read} of ${plural(subs.listed, "subscription")} read`
              : run.status}
          {failed.map((x) => (
            <div key={x.id}>
              {x.name}: {x.error || "could not be read"}
            </div>
          ))}
          {unreadable.map((x) => (
            <div key={x.name}>
              {x.name} not readable since <DateTime value={x.last_read_at} mode="date" fallback="the first pull" />
            </div>
          ))}
        </>
      ),
    },
    ...(counts?.resources != null
      ? [
          {
            label: "Found",
            value: `${counts.resources.toLocaleString()} resources · ${counts.added ?? 0} added · ${counts.changed ?? 0} changed · ${counts.removed ?? 0} removed`,
          },
        ]
      : []),
  ];
}

export function AzureSources({ canManage, onChanged }: { canManage: boolean; onChanged: () => void }) {
  const toast = useToast();
  const canConfigure = useCan(PERMISSIONS.pluginsManage);
  const [status, setStatus] = useState<AzureStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const wasRunning = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await getAzureStatus();
      if (!next.ok) throw new Error(next.error || "Could not load the Azure status.");
      setStatus(next);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll only while a pull runs; tell the screen around us when it ends.
  const running = Boolean(status?.running);
  useEffect(() => {
    if (running) {
      wasRunning.current = true;
      const timer = setInterval(() => void refresh(), POLL_MS);
      return () => clearInterval(timer);
    }
    if (wasRunning.current) {
      wasRunning.current = false;
      onChanged();
    }
  }, [running, refresh, onChanged]);

  async function pull() {
    setStarting(true);
    try {
      const result = await startAzurePull();
      if (!result.ok) throw new Error(result.error || "The pull was refused.");
      await refresh();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setStarting(false);
    }
  }

  if (!status) {
    return error ? (
      <CenteredMessage
        title="Could not load Azure"
        detail={error}
        action={
          <Button variant="primary" onClick={() => void refresh()}>
            Retry
          </Button>
        }
      />
    ) : (
      <CenteredMessage title="Loading Azure…" />
    );
  }

  const chip = cardStatus(status);
  const run = status.running ?? status.last_run;
  const expires = status.credential.expires_in_days;
  const unknown = status.last_run?.counts?.unknown_types ?? [];
  const canPull = canManage && status.enabled && status.configured && !status.running && !starting;

  return (
    <div className={styles.stack} data-testid="azure-sources">
      {!status.enabled && <Notice>Azure is off. This is the picture from the last pull.</Notice>}
      {status.test_endpoints && (
        <Notice tone="warn">
          <span data-testid="azure-test-endpoints">This instance reads a test Azure, not Microsoft.</span>
        </Notice>
      )}
      {!status.verified_against_real_tenant && (
        <Notice>Not verified against a real tenant yet. Check what you see against the Azure portal.</Notice>
      )}

      <Card title="Azure" actions={<Tag tone={chip.tone}><span data-testid="azure-status">{chip.label}</span></Tag>}>
        <div className={styles.stack}>
          {status.enabled && !status.configured && (
            <Notice tone="warn">
              <span data-testid="azure-not-set-up">
                Azure is not set up. {canConfigure ? (
                  <>
                    Open <Link to="/settings/azure">Settings, Azure</Link> to set it up.
                  </>
                ) : (
                  "An administrator does this under Settings, Azure."
                )}
              </span>
            </Notice>
          )}

          {status.running && (
            <Notice tone="accent">
              <span data-testid="azure-progress">Pull running: {progress(status.running)}</span>
            </Notice>
          )}
          {!status.running && run?.status === "failed" && run.error && <Notice tone="crit">{run.error}</Notice>}

          <KV
            rows={[
              ...(status.last_run ? lastRunRows(status, status.last_run) : [{ label: "Last pull", value: "Never" }]),
              ...(status.enabled && status.configured
                ? [{ label: "Next pull", value: status.next_pull_at ? <DateTime value={status.next_pull_at} /> : "Schedule is off" }]
                : []),
              {
                label: "Credential",
                value: status.credential.kind ? (
                  <>
                    {status.credential.kind === "certificate" ? "Certificate" : "Client secret"}
                    {status.credential.expires_on && (
                      <>
                        {" "}
                        · expires <DateTime value={status.credential.expires_on} mode="date" />
                      </>
                    )}
                  </>
                ) : (
                  "not set"
                ),
              },
              ...(unknown.length ? [{ label: "Unknown types", value: `${unknown.length} · filed under Other` }] : []),
              { label: "Writes back", value: "Never: read only" },
            ]}
          />
          {expires !== null && expires <= 30 && (
            <Notice tone={expires < 0 ? "crit" : "warn"}>
              <span data-testid="azure-expiry">
                {expires < 0
                  ? "The credential has expired. Pulls will fail until it is replaced."
                  : `The credential expires in ${plural(expires, "day")}. Replace it under Settings, Azure.`}
              </span>
            </Notice>
          )}

          <div className={styles.actions}>
            <Button variant="primary" disabled={!canPull} data-testid="azure-pull" onClick={() => void pull()}>
              {status.running || starting ? "Pulling…" : "Pull now"}
            </Button>
            {canConfigure && (
              <Link to="/settings/azure">
                <Button>Settings</Button>
              </Link>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
