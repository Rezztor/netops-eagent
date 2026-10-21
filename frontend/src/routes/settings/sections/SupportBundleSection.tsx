/**
 * The support bundle: the machine's logs and settings, made and cleaned of secrets by the host's update
 * service, for the administrator to download and send to Rezztor. The same bundle is written to a USB stick
 * from the console menu when the product is down.
 */

import { useEffect, useState } from "react";

import { readableSize } from "@/api/attachments";
import { downloadSupportBundle, fetchUpdateStatus, requestUpdate, type UpdateStatus } from "@/api/updates";
import { Button, Card, DateTime, Mono, ScopeNote, Table } from "@/components";
import { useAsync } from "@/lib/useAsync";
import { SectionError, SectionLoading } from "../parts";

const POLL_MS = 2000;

export function SupportBundleSection() {
  const { state, reload } = useAsync(() => fetchUpdateStatus(), []);

  if (state.status === "loading") return <SectionLoading title="Support bundle" />;
  if (state.status === "error") {
    return <SectionError title="Support bundle" message={state.error.message} onRetry={reload} />;
  }
  return <SupportBundleDetails initial={state.data} />;
}

function SupportBundleDetails({ initial }: { initial: UpdateStatus }) {
  const [status, setStatus] = useState(initial);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pending) return;
    const id = window.setInterval(() => {
      void fetchUpdateStatus()
        .then((next) => {
          setStatus(next);
          // The host puts the request's id into the state when the bundle is written, or could not be.
          if (next.available && next.request === pending) setPending(null);
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [pending]);

  if (!status.available || status.bundles === undefined) {
    return (
      <Card title="Support bundle" description="The logs and settings of this machine, for Rezztor's support.">
        <ScopeNote>
          {status.available
            ? "This installation's update service makes no support bundle yet. An update from Rezztor brings it."
            : "This installation has no host service to make one. Collect the logs as its installation guide says."}
        </ScopeNote>
      </Card>
    );
  }

  const working = status.phase === "installing" || status.phase === "restarting";

  async function make() {
    setError(null);
    try {
      const result = await requestUpdate("bundle");
      if (!result.ok) setError(result.error ?? "The update service refused the request.");
      else if (result.request) setPending(result.request);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function download(file: string) {
    setError(null);
    try {
      await downloadSupportBundle(file);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  const shownError = error ?? (pending ? null : status.bundle_error ?? null);
  return (
    <>
      <Card
        title="Support bundle"
        description="The logs and settings of this machine in one file, for Rezztor's support to find a fault."
        actions={
          <Button variant="primary" onClick={() => void make()} disabled={pending !== null || working}>
            {pending ? "Making the bundle…" : "Make a bundle"}
          </Button>
        }
      >
        {shownError && <ScopeNote tone="crit">{shownError}</ScopeNote>}
        {working && <ScopeNote>An update is being installed. Make a bundle when it has finished.</ScopeNote>}
        <ScopeNote>
          In it: the version, the status of the machine and its containers, the last 2,000 lines of each
          container&apos;s log, 7 days of the system journal, the network, disk and memory. Never in it: passwords,
          keys, certificates, the database, users&apos; files or conversations; what looks like a secret is blanked,
          and the file REDACTED.txt inside says where. It reaches Rezztor only when you send it.
        </ScopeNote>
      </Card>
      <Card title="Bundles" description="Kept on this machine for 7 days; the five newest.">
        {status.bundles.length === 0 ? (
          <ScopeNote>None yet.</ScopeNote>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Made</th>
                <th>File</th>
                <th>Size</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {status.bundles.map((bundle) => (
                <tr key={bundle.file}>
                  <td>
                    <DateTime value={bundle.at} />
                  </td>
                  <td>
                    <Mono>{bundle.file}</Mono>
                  </td>
                  <td>{readableSize(bundle.size)}</td>
                  <td>
                    <Button variant="ghost" onClick={() => void download(bundle.file)}>
                      Download
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
