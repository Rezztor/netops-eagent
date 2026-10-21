/**
 * The licence page: what this installation is licensed for, and the actions
 * an administrator has: enter an activation key, check in, release the
 * installation, and the offline exchange (request code out, licence file in).
 *
 * The only section that fetches on open rather than from the shared bundle.
 * The index chip comes from `session.licence`, already in the bootstrap
 * payload for the app-wide banner; the full status (customer, seats, devices, features)
 * lives behind `settings.manage` on `license_status`.
 */

import { useState } from "react";

import {
  activateLicence,
  checkInLicence,
  getLicenceStatus,
  installLicenceFile,
  releaseInstallation,
  requestActivationCode,
  type LicenceActionResult,
  type LicenceState,
  type LicenceStatus,
} from "@/api/licence";
import {
  Button,
  Card,
  DateTime,
  Mono,
  Row,
  ScopeNote,
  Tag,
  TextInput,
  useConfirm,
  type TagTone,
} from "@/components";
import { useReloadSession } from "@/lib/session";
import { useAsync } from "@/lib/useAsync";
import { SectionError, SectionLoading } from "../parts";

export const LICENCE_STATE_TONE: Record<LicenceState, TagTone> = {
  licensed: "ok",
  // Grace is full function with a countdown: warn, not crit.
  grace1: "warn",
  grace2: "warn",
  // Restricted is the one state in which nothing new starts.
  restricted: "crit",
};

export const LICENCE_STATE_LABEL: Record<LicenceState, string> = {
  licensed: "Licensed",
  grace1: "Grace",
  grace2: "Grace, final period",
  restricted: "Restricted",
};

const days = (count: number) => `${count} ${count === 1 ? "day" : "days"}`;

const textArea = {
  width: "100%",
  maxWidth: 520,
  fontFamily: "var(--font-mono, ui-monospace, monospace)",
  fontSize: "var(--text-sm)",
  padding: "var(--space-2)",
  borderRadius: 6,
  border: "1px solid var(--line)",
  background: "var(--bg)",
  color: "var(--ink)",
} as const;

export function LicenceSection() {
  const { state, reload } = useAsync(() => getLicenceStatus(), []);

  if (state.status === "loading") return <SectionLoading title="Licence" />;
  if (state.status === "error") {
    return <SectionError title="Licence" message={state.error.message} onRetry={reload} />;
  }
  return <LicenceDetails initial={state.data} />;
}

type Busy = null | "activate" | "checkin" | "release" | "install" | "request";

function LicenceDetails({ initial }: { initial: LicenceStatus }) {
  // Every action answers with the refreshed status, so this re-renders from
  // the server's own truth rather than from a guess about what an action did.
  const [status, setStatus] = useState(initial);
  // The app-wide banner and the Settings index chip read `session.licence`.
  const reloadSession = useReloadSession();
  const { confirm } = useConfirm();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [offline, setOffline] = useState(false);
  const [licenceFile, setLicenceFile] = useState("");
  const [deploymentName, setDeploymentName] = useState("");
  const [requestCode, setRequestCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const licence = status.licence;
  const overSeats = licence !== null && status.seats_used > licence.seats;
  const inGrace = status.state === "grace1" || status.state === "grace2";

  async function run(what: Busy, call: () => Promise<LicenceActionResult>, done: string) {
    setBusy(what);
    setError(null);
    setNotice(null);
    try {
      const result = await call();
      if (result.licence) setStatus(result.licence);
      if (result.request_code) setRequestCode(result.request_code);
      if (result.ok) {
        setNotice(done);
        reloadSession();
      } else {
        setError(result.error ?? "The licence server refused the request.");
      }
      return result.ok;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function activate() {
    if (!key.trim()) {
      setError("Enter the activation key you were given.");
      return;
    }
    // The key is not kept on screen once it has done its work.
    if (await run("activate", () => activateLicence(key), "The licence is active.")) setKey("");
  }

  async function release() {
    const yes = await confirm({
      title: "Release this installation?",
      body:
        "The licence server frees this installation's place so the licence can be activated on another server. " +
        "This installation is then restricted: new agent runs, device and cloud changes and scheduled tasks " +
        "do not start until it is activated again. Nothing is deleted.",
      confirmLabel: "Release",
      danger: true,
    });
    if (yes) await run("release", releaseInstallation, "The installation was released.");
  }

  return (
    <Card
      title="Licence"
      description="What this installation is licensed for, and its standing with the licence server."
    >
      <Row label="Status">
        <Tag tone={LICENCE_STATE_TONE[status.state]}>{LICENCE_STATE_LABEL[status.state]}</Tag>
        {status.message && (
          <span style={{ color: "var(--ink-3)", fontSize: "var(--text-sm)" }}>{status.message}</span>
        )}
      </Row>

      {inGrace && status.days_left !== null && (
        <ScopeNote tone={status.state === "grace2" ? "crit" : "warn"}>
          The licence could not be renewed. Everything works for {days(status.days_left)} more; after that
          new agent runs, device and cloud changes and scheduled tasks do not start. Check that this server
          can reach the licence server, or load a licence file.
        </ScopeNote>
      )}
      {status.reason === "revoked_notice" && status.days_left !== null && (
        <ScopeNote tone="crit">
          This licence has been revoked. Everything works for {days(status.days_left)} more. Contact Rezztor.
        </ScopeNote>
      )}
      {status.state === "restricted" && (
        <ScopeNote tone="crit">
          New agent runs, device and cloud changes and scheduled tasks do not start. Sign-in, reading,
          export and settings keep working, and nothing is deleted.
        </ScopeNote>
      )}
      {status.devices_warning === "over" && (
        <ScopeNote tone="warn">
          This installation has {status.devices_used} network devices and the licence allows{" "}
          {status.devices_allowed}. Everything keeps working; decommission or mark devices spare, or ask
          Rezztor for a larger licence.
        </ScopeNote>
      )}
      {status.devices_warning === "near" && (
        <ScopeNote>
          This installation has {status.devices_used} of the {status.devices_allowed} network devices the
          licence allows.
        </ScopeNote>
      )}
      {status.clock_behind && (
        <ScopeNote tone="warn">
          This server's clock reads earlier than the latest time the licence has seen. The later time is
          used, and nothing is extended until a check-in succeeds.
        </ScopeNote>
      )}

      {licence && (
        <>
          <Row label="Customer">
            <Mono>{licence.customer}</Mono>
          </Row>
          <Row label="Licence">
            <Mono>
              {licence.lic} · version {licence.ver}
            </Mono>
            {licence.kind === "trial" && <Tag tone="accent">trial</Tag>}
            {licence.kind === "lab" && <Tag tone="accent">lab build, licence check off</Tag>}
            {licence.offline && <Tag tone="muted">offline</Tag>}
          </Row>
          <Row label="Edition">
            <Mono>{licence.edition}</Mono>
          </Row>
          <Row label="Seats" help="Active accounts use a seat; disabled ones do not.">
            <span style={{ color: overSeats ? "var(--crit)" : undefined }}>
              <Mono>
                {status.seats_used} used of {licence.seats}
              </Mono>
            </span>
            {overSeats && (
              <span style={{ color: "var(--crit)", fontSize: "var(--text-sm)" }}>
                more than the licence allows
              </span>
            )}
          </Row>
          <Row
            label="Devices"
            help="Active devices in the inventory, and Azure firewalls, load balancers and gateways. Over the limit the page warns; nothing is refused."
          >
            <span style={{ color: status.devices_warning === "over" ? "var(--warn)" : undefined }}>
              <Mono>
                {status.devices_allowed === null
                  ? `${status.devices_used}, no limit`
                  : `${status.devices_used} of ${status.devices_allowed}`}
              </Mono>
            </span>
          </Row>
          <Row label="Features">
            {licence.features.length ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {licence.features.map((feature) => (
                  <Tag key={feature} tone="muted">
                    {feature}
                  </Tag>
                ))}
              </div>
            ) : (
              <Tag tone="muted">none</Tag>
            )}
          </Row>
          <Row label="Contract ends">
            <DateTime value={licence.contract_end} mode="date" />
          </Row>
          {!licence.offline && (
            <Row
              label="Lease ends"
              help="The daily check-in renews the lease. If it cannot, the installation keeps working until this date and then through the grace period."
            >
              <DateTime value={status.lease_end} fallback="no lease" />
            </Row>
          )}
        </>
      )}

      {!licence?.offline && (
        <Row label="Last check-in" help="Once a day, in the background. Nobody waits for it.">
          <DateTime value={status.last_success_at} fallback="never" />
          {status.last_result && status.last_result !== "ok" && (
            <span style={{ color: "var(--warn)", fontSize: "var(--text-sm)" }}>
              Last attempt, <DateTime value={status.last_attempt_at} />: {status.last_result}
              {status.pending && " It is tried again by itself."}
            </span>
          )}
          {status.last_result === "ok" && <Tag tone="ok">ok</Tag>}
        </Row>
      )}
      <Row
        label="This installation"
        help="A random id made on first start. It is not derived from hardware."
      >
        <Mono>{status.installation_id}</Mono>
      </Row>

      {!offline && (
        <Row
          label="Activation key"
          help="The key Rezztor gave you: REZ- and five groups of five characters. It is checked for typing mistakes before anything is sent."
        >
          <TextInput
            aria-label="Activation key"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder="REZ-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"
            autoComplete="off"
            spellCheck={false}
            disabled={busy !== null}
          />
        </Row>
      )}

      {offline && (
        <>
          <Row
            label="Deployment name"
            help="How you refer to this server, for example “DR site”. It labels the request; the licence is bound to the installation id, not to this name."
          >
            <TextInput
              aria-label="Deployment name"
              value={deploymentName}
              onChange={(event) => setDeploymentName(event.target.value)}
              placeholder="SOC-primary"
              disabled={busy !== null}
            />
          </Row>
          <Row
            label="Request code"
            help="Send this to Rezztor. It names this installation and nothing else; making it sends nothing."
          >
            <Button
              onClick={() => {
                if (!deploymentName.trim()) {
                  setError("Name this deployment before making a request code.");
                  return;
                }
                void run("request", () => requestActivationCode(deploymentName), "Request code made.");
              }}
              disabled={busy !== null}
            >
              {busy === "request" ? "Making…" : "Make request code"}
            </Button>
          </Row>
          {requestCode && (
            <div style={{ padding: "var(--space-2) 0" }}>
              <textarea
                aria-label="Request code"
                value={requestCode}
                readOnly
                onClick={(event) => event.currentTarget.select()}
                rows={3}
                spellCheck={false}
                style={textArea}
              />
              <div style={{ paddingTop: 6 }}>
                <Button
                  variant="ghost"
                  onClick={() => {
                    void navigator.clipboard
                      ?.writeText(requestCode)
                      .then(() => setNotice("Request code copied."));
                  }}
                >
                  Copy request code
                </Button>
              </div>
            </div>
          )}
          <Row
            label="Licence file"
            help="The file Rezztor sent back (.rezlic). Choose it or paste its text. It is verified for this installation before it is stored, so a wrong file cannot replace a working licence."
          >
            <input
              type="file"
              aria-label="Choose licence file"
              accept=".rezlic,.json,.txt"
              disabled={busy !== null}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void file.text().then(setLicenceFile);
              }}
            />
            <textarea
              aria-label="Licence file"
              value={licenceFile}
              onChange={(event) => setLicenceFile(event.target.value)}
              placeholder='{"v":2,"typ":"licence", …}'
              disabled={busy !== null}
              rows={4}
              spellCheck={false}
              style={textArea}
            />
          </Row>
        </>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", padding: "var(--space-3) 0" }}>
        {!offline && (
          <>
            <Button variant="primary" onClick={() => void activate()} disabled={busy !== null}>
              {busy === "activate" ? "Activating…" : "Activate"}
            </Button>
            {(status.can_release || status.pending) && (
              <Button
                onClick={() => void run("checkin", checkInLicence, "Checked in with the licence server.")}
                disabled={busy !== null}
              >
                {busy === "checkin" ? "Checking…" : status.pending ? "Try again now" : "Check in now"}
              </Button>
            )}
            {status.can_release && (
              <Button variant="danger" onClick={() => void release()} disabled={busy !== null}>
                {busy === "release" ? "Releasing…" : "Release this installation"}
              </Button>
            )}
            <Button variant="ghost" onClick={() => setOffline(true)} disabled={busy !== null}>
              No internet access?
            </Button>
          </>
        )}
        {offline && (
          <>
            <Button
              variant="primary"
              onClick={() => {
                if (!licenceFile.trim()) {
                  setError("Choose the licence file, or paste its text.");
                  return;
                }
                void run("install", () => installLicenceFile(licenceFile), "The licence file was loaded.");
              }}
              disabled={busy !== null}
            >
              {busy === "install" ? "Loading…" : "Load licence file"}
            </Button>
            <Button variant="ghost" onClick={() => setOffline(false)} disabled={busy !== null}>
              Back
            </Button>
          </>
        )}
      </div>

      {error && <ScopeNote tone="crit">{error}</ScopeNote>}
      {notice && <ScopeNote tone="accent">{notice}</ScopeNote>}
    </Card>
  );
}
