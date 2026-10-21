/**
 * The Updates page: install a signed `.rezupd` from Rezztor without rolling a
 * new image by hand, either from the list Rezztor's update service gives the
 * host (ADR 0091) or as a file. The host does the work; this page reads state,
 * uploads a file, and writes a request.
 */

import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";

import {
  downloadUpdate,
  fetchUpdateStatus,
  requestUpdate,
  setDailyCheck,
  uploadUpdate,
  type ListedUpdate,
  type UpdateAvailable,
  type UpdateStatus,
  type UpdateStep,
  type UpdatesList,
} from "@/api/updates";
import {
  Button,
  Card,
  DateTime,
  Mono,
  Row,
  ScopeNote,
  Table,
  Tag,
  Toggle,
  useConfirm,
} from "@/components";
import { readableSize } from "@/api/attachments";
import { useReloadSession } from "@/lib/session";
import { useAsync } from "@/lib/useAsync";
import { SectionError, SectionLoading } from "../parts";

const POLL_MS = 2000;

const STEP_MARK: Record<UpdateStep["state"], string> = {
  done: "✓",
  running: "●",
  waiting: "○",
  failed: "✕",
};

export function UpdatesSection() {
  const { state, reload } = useAsync(() => fetchUpdateStatus(), []);

  if (state.status === "loading") return <SectionLoading title="Updates" />;
  if (state.status === "error") {
    return <SectionError title="Updates" message={state.error.message} onRetry={reload} />;
  }
  return <UpdatesDetails initial={state.data} />;
}

type Busy = null | "upload" | "install" | "restart" | "discard" | "refresh" | "download" | "update_all" | "daily" | "reboot";

/** The host's answers to a restart of the appliance; said on its card, not on the file card. */
const REBOOT_CODES = new Set(["no_reboot", "reboot"]);

/** A package's Debian part (ADR 0092) in one line. */
function debianLine(count: number, reboot: boolean): string {
  return `Debian security updates: ${count} package${count === 1 ? "" : "s"}${reboot ? ", restart of the appliance needed" : ""}`;
}

/**
 * What the host's codes mean, in plain words, for the questions to the update
 * service and for a download or Update to latest. A code not here or below
 * (the checks of an uploaded file) is shown with the host's own text.
 */
const SERVICE_MESSAGES: Record<string, string> = {
  not_a_release: "This build takes no update packages: it is a lab or development build.",
  not_installed: "The product is not installed on this machine yet, so there is nothing to update.",
  no_key: "This machine has no update key, so it cannot ask the update service. Contact Rezztor support.",
  bad_answer: "The update service did not accept this installation's answer. Try again in a few minutes.",
  bad_challenge:
    "The update service asked a question this installation does not answer, so nothing was sent. Tell Rezztor.",
  bad_service_answer: "The update service sent an answer this version cannot read. Try again later.",
  service_busy: "The update service is busy. Try again in an hour.",
  service: "The update service answered with an error. Try again later.",
  up_to_date: "There is nothing to download: this installation runs the latest version.",
  not_next: "Updates are installed in order, and another one comes first. Press Check now to see the list again.",
  upgrade_required:
    "Your plan does not include installing updates from here. Upgrade to REZ-Pro, or download the update from the customer portal and install it as a file below.",
  not_yet_available: "This update is not available to this installation yet. Check again later.",
};

/** Codes whose host text names the version, size or date and is already plain: said as the host wrote it. */
const HOST_TEXT = new Set(["not_available", "too_large", "download_incomplete", "bad_download", "space", "rolled_back"]);

/** The notes of ServiceError, which say more than a sentence. */
const NOTED = new Set(["unreachable", "unknown_installation", "chain_gap"]);

const WALKED = /^\d+ of \d+ updates installed/;

/** A state error that came from a download or Update to latest, so it is said on the list, not on the file card. */
function fromTheList(error: { code: string; text: string }): boolean {
  if (WALKED.test(error.text)) return true;
  if (error.code === "space") return error.text.includes("download");
  if (error.code === "rolled_back") return false;
  return error.code in SERVICE_MESSAGES || HOST_TEXT.has(error.code) || NOTED.has(error.code);
}

/** One plain sentence for a host error from the update service, a download or Update to latest. */
function serviceMessage(error: { code: string; text: string }): string {
  // Update to latest says first how far it got; that count is the news, then the host's reason.
  if (WALKED.test(error.text) || HOST_TEXT.has(error.code) || NOTED.has(error.code)) {
    return error.code === "too_large" ? `${error.text} Install it from a file on the console.` : error.text;
  }
  return SERVICE_MESSAGES[error.code] ?? error.text;
}

/**
 * The host is walking the list (Update to latest). Its phases come and go
 * between packages, so the page follows `progress` instead.
 * ponytail: guessed from progress and the absence of an error; a host field
 * saying "walking" would be exact if the two ever disagree.
 */
function walking(status: UpdateAvailable): boolean {
  const progress = status.progress;
  return Boolean(progress && progress.done < progress.total && !status.error && !status.updates?.error);
}

function UpdatesDetails({ initial }: { initial: UpdateStatus }) {
  const [status, setStatus] = useState(initial);
  const [pendingRequest, setPendingRequest] = useState<string | null>(null);
  // What the pending request asked for, so Check now can say it is checking.
  const [pendingWhat, setPendingWhat] = useState<Busy>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  // Which action failed, so the list's failures are said on the list and an upload's on the drop zone.
  const [errorFrom, setErrorFrom] = useState<Busy>(null);
  const [copied, setCopied] = useState(false);
  const [dragging, setDragging] = useState(false);
  // Survives failed status calls while the application is coming back.
  const [expectRestart, setExpectRestart] = useState(
    () => initial.available && initial.phase === "restarting",
  );
  // The whole appliance restarts (Debian's updates): away for a few minutes, back when it no longer needs one.
  const [expectReboot, setExpectReboot] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const lastPhase = useRef(initial.available ? initial.phase : null);
  // "Download and install": once the host has checked the file it downloaded, install it.
  const [installWhenChecked, setInstallWhenChecked] = useState<string | null>(null);
  const reloadSession = useReloadSession();
  const { confirm } = useConfirm();

  // Until the host has taken a request the phase is still the old one, whatever it is.
  const shouldPoll =
    status.available &&
    (status.phase === "installing" ||
      status.phase === "restarting" ||
      status.phase === "downloading" ||
      walking(status) ||
      installWhenChecked !== null ||
      expectRestart ||
      expectReboot ||
      pendingRequest !== null);

  useEffect(() => {
    if (!shouldPoll) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await fetchUpdateStatus();
        if (cancelled) return;
        setStatus(next);
        // The host puts the request's id and the new phase into the state together.
        const taken = !pendingRequest || (next.available && next.request === pendingRequest);
        if (pendingRequest && taken) setPendingRequest(null);
        if (expectRestart && taken && next.available && next.phase !== "restarting") {
          setExpectRestart(false);
        }
        // Back after the restart of the appliance, or the host refused it.
        if (expectReboot && taken && next.available && (!next.os?.reboot_needed || next.error)) {
          setExpectReboot(false);
        }
        const wanted = installWhenChecked;
        if (taken && wanted && next.available && next.phase !== "downloading") {
          setInstallWhenChecked(null);
          if (next.phase === "checked" && next.package?.version === wanted && !next.error) {
            void requestUpdate("install")
              .then((result) => {
                if (result.ok && result.request) setPendingRequest(result.request);
                else setLocalError(result.error ?? "The update service refused the request.");
              })
              .catch((caught: unknown) => setLocalError(caught instanceof Error ? caught.message : String(caught)));
          }
        }
        // The banner and the version in the rail come from the session: when an update starts to
        // wait, stops waiting, or ends either way, they are read again.
        if (next.available && taken) {
          const before = lastPhase.current;
          lastPhase.current = next.phase;
          const moved = before !== next.phase && (next.phase === "staged" || next.phase === "idle");
          if (moved && (before === "installing" || before === "staged" || before === "restarting")) {
            reloadSession();
          }
        }
      } catch {
        // While restarting, a failed call is expected: keep asking.
      }
    };
    const id = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [shouldPoll, pendingRequest, expectRestart, expectReboot, installWhenChecked, reloadSession]);

  async function runAction(
    what: Busy,
    call: () => Promise<{ ok: boolean; request?: string; error?: string }>,
  ) {
    setBusy(what);
    setLocalError(null);
    setErrorFrom(what);
    try {
      const result = await call();
      if (!result.ok) {
        setInstallWhenChecked(null);
        setLocalError(result.error ?? "The update service refused the request.");
        return;
      }
      if (result.request) {
        setPendingRequest(result.request);
        setPendingWhat(what);
      }
      if (what === "restart") setExpectRestart(true);
      if (what === "reboot") setExpectReboot(true);
      if (what !== "download") setInstallWhenChecked(null);
      // The first poll, a moment later, shows what the host made of it.
    } catch (caught) {
      setInstallWhenChecked(null);
      if (what === "reboot") {
        // The appliance may go away before the answer arrives.
        setExpectReboot(true);
        return;
      }
      if (what === "restart" || expectRestart) {
        // Restart may take the application away before the answer arrives.
        setExpectRestart(true);
        setStatus((prev) => (prev.available ? { ...prev, phase: "restarting" } : prev));
        return;
      }
      setLocalError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
      setUploadPercent(null);
    }
  }

  async function updateAll(count: number) {
    const yes = await confirm({
      title: `Install all ${count} updates?`,
      body:
        "They are downloaded and installed one after another, in order. The application restarts after each one " +
        "that needs it, for about a minute each time; agent runs in progress stop. If one does not start, the " +
        "appliance goes back to the version before it and stops there.",
      confirmLabel: "Update to latest",
    });
    if (yes) await runAction("update_all", () => requestUpdate("update_all"));
  }

  async function toggleDaily(next: boolean) {
    const before = status.available ? status.daily_check !== false : true;
    setStatus((prev) => (prev.available ? { ...prev, daily_check: next } : prev));
    setBusy("daily");
    setLocalError(null);
    setErrorFrom("daily");
    try {
      const result = await setDailyCheck(next);
      if (!result.ok) throw new Error(result.error ?? "The setting was not saved.");
    } catch (caught) {
      setStatus((prev) => (prev.available ? { ...prev, daily_check: before } : prev));
      setLocalError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".rezupd")) {
      setErrorFrom("upload");
      setLocalError("Choose a file ending in .rezupd.");
      return;
    }
    setUploadPercent(0);
    await runAction("upload", () =>
      uploadUpdate(file, (percent) => setUploadPercent(percent)),
    );
  }

  async function restartNow() {
    const yes = await confirm({
      title: "Restart to finish the update?",
      body:
        "The application is away for about a minute. People stay signed in; agent runs in progress stop. " +
        "This page comes back by itself. If the new version does not start, the appliance returns to the version you have now.",
      confirmLabel: "Restart now",
      danger: true,
    });
    if (yes) await runAction("restart", () => requestUpdate("restart"));
  }

  async function rebootNow() {
    const yes = await confirm({
      title: "Restart the appliance?",
      body:
        "The Debian updates are in force after the restart. The appliance is away for a few minutes; agent runs in " +
        "progress stop. This page comes back by itself.",
      confirmLabel: "Restart the appliance",
      danger: true,
    });
    if (yes) await runAction("reboot", () => requestUpdate("reboot"));
  }

  if (!status.available) {
    return (
      <Card
        title="Updates"
        description="Install a fix from Rezztor without installing again."
      >
        <ScopeNote>
          This installation has no update service. It is upgraded by rolling a new image.
        </ScopeNote>
      </Card>
    );
  }

  if (expectReboot) {
    return <RebootingView />;
  }

  if (status.phase === "restarting" || expectRestart) {
    const version = status.package?.version ?? status.result?.version;
    // The new version answers and Debian's updates go in: the application is there, the page says so.
    const system = status.steps.find((step) => step.id === "system" && step.state === "running");
    return <RestartingView version={version} debian={Boolean(system)} />;
  }

  if (walking(status)) {
    return <WalkingView status={status} />;
  }

  if (status.phase === "downloading") {
    return <DownloadingView status={status} />;
  }

  if (status.phase === "checked" && status.package) {
    return (
      <CheckedView
        status={status}
        busy={busy}
        localError={localError ?? status.error?.text ?? null}
        onDiscard={() => void runAction("discard", () => requestUpdate("discard"))}
        onInstall={() => void runAction("install", () => requestUpdate("install"))}
      />
    );
  }

  if (status.phase === "installing") {
    return <InstallingView status={status} />;
  }

  if (status.phase === "staged" && status.package) {
    return (
      <StagedView
        status={status}
        busy={busy}
        localError={localError ?? status.error?.text ?? null}
        onRemove={() => void runAction("discard", () => requestUpdate("discard"))}
        onRestart={() => void restartNow()}
      />
    );
  }

  // idle
  return (
    <IdleView
      listed={
        <AvailableCard
          status={status}
          busy={busy}
          pending={pendingRequest !== null}
          checking={pendingRequest !== null && pendingWhat === "refresh"}
          error={errorFrom !== "upload" ? localError : null}
          onRefresh={() => void runAction("refresh", () => requestUpdate("refresh"))}
          onDownload={(version) => {
            setInstallWhenChecked(version);
            void runAction("download", () => downloadUpdate(version));
          }}
          onUpdateAll={(count) => void updateAll(count)}
          onDaily={(next) => void toggleDaily(next)}
        />
      }
      status={status}
      busy={busy}
      onReboot={() => void rebootNow()}
      uploadPercent={uploadPercent}
      localError={errorFrom === "upload" ? localError : null}
      copied={copied}
      dragging={dragging}
      fileInput={fileInput}
      onCopy={() => {
        void navigator.clipboard?.writeText(status.update_key).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 2000);
        });
      }}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        void onFile(event.dataTransfer.files?.[0]);
      }}
      onChoose={() => fileInput.current?.click()}
      onFileChange={(file) => void onFile(file)}
    />
  );
}

function IdleView({
  listed,
  status,
  busy,
  onReboot,
  uploadPercent,
  localError,
  copied,
  dragging,
  fileInput,
  onCopy,
  onDragOver,
  onDragLeave,
  onDrop,
  onChoose,
  onFileChange,
}: {
  listed: ReactNode;
  status: UpdateAvailable;
  busy: Busy;
  onReboot: () => void;
  uploadPercent: number | null;
  localError: string | null;
  copied: boolean;
  dragging: boolean;
  fileInput: React.RefObject<HTMLInputElement | null>;
  onCopy: () => void;
  onDragOver: (event: DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent) => void;
  onChoose: () => void;
  onFileChange: (file: File | undefined) => void;
}) {
  const { installed, update_key, result, history } = status;
  // A download's or Update to latest's failure is said on the list.
  const error =
    status.error && !fromTheList(status.error) && !REBOOT_CODES.has(status.error.code) ? status.error : null;
  const versionLabel =
    installed.version === installed.base
      ? installed.version
      : `${installed.version} on release ${installed.base}`;

  return (
    <>
      <Card
        title="This installation"
        description="What is running now."
        actions={<Tag tone="muted">no update waiting</Tag>}
      >
        {result && (
          <ScopeNote tone={result.outcome === "installed" ? "accent" : "crit"}>
            {result.text}
          </ScopeNote>
        )}
        <Row label="Version" help="The release and the updates on top of it.">
          <Mono>{versionLabel}</Mono>
        </Row>
        <Row label="Last update" help="When, and who started it.">
          {installed.updated_at ? (
            <DateTime value={installed.updated_at} />
          ) : (
            <span>none since installation</span>
          )}
        </Row>
        {status.os?.error && <ScopeNote tone="crit">{status.os.error.text}</ScopeNote>}
        <Row
          label="Update key of this installation"
          help="Rezztor needs it once, to make packages this machine can open."
        >
          <Mono>{update_key}</Mono>
          <Button variant="ghost" onClick={onCopy} disabled={busy !== null}>
            {copied ? "Copied" : "Copy"}
          </Button>
        </Row>
      </Card>

      {status.os?.reboot_needed && <RebootCard status={status} busy={busy} onReboot={onReboot} />}

      {listed}

      <Card
        title="Install an update from a file"
        description="A file from Rezztor, ending in .rezupd: from the customer portal, or for a site that does not reach the internet."
      >
        {(error || localError) && (
          <ScopeNote tone="crit">{localError ?? error?.text}</ScopeNote>
        )}
        <div
          role="button"
          tabIndex={0}
          aria-label="Drop the update file here"
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") onChoose();
          }}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "var(--space-2)",
            padding: "var(--space-6)",
            border: `1px dashed ${dragging ? "var(--accent)" : "var(--line-2)"}`,
            borderRadius: "var(--radius)",
            background: dragging ? "var(--accent-wash)" : "var(--surface-alt)",
            textAlign: "center",
          }}
        >
          <b style={{ color: "var(--ink)", fontSize: "var(--text-md)" }}>Drop the update file here</b>
          <span style={{ color: "var(--ink-3)", fontSize: "var(--text-sm)" }}>or</span>
          <Button variant="primary" onClick={onChoose} disabled={busy !== null}>
            {busy === "upload"
              ? uploadPercent !== null
                ? `Uploading… ${uploadPercent}%`
                : "Uploading…"
              : "Choose file…"}
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept=".rezupd"
            aria-label="Choose update file"
            hidden
            disabled={busy !== null}
            onChange={(event) => {
              onFileChange(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </div>
        {uploadPercent !== null && busy === "upload" && (
          <Bar percent={uploadPercent} label="Upload progress" />
        )}
        <ScopeNote>
          A large file, or a browser that cannot reach this page? Copy the file to the appliance with a
          USB stick or SSH and choose “Apply an update” on the console.
        </ScopeNote>
      </Card>

      {history.length > 0 && <HistoryCard history={history} />}
    </>
  );
}

function CheckedView({
  status,
  busy,
  localError,
  onDiscard,
  onInstall,
}: {
  status: UpdateAvailable;
  busy: Busy;
  localError: string | null;
  onDiscard: () => void;
  onInstall: () => void;
}) {
  const pkg = status.package!;
  return (
    <Card
      title={`Update ${pkg.version}`}
      description={`${readableSize(pkg.size)} · made ${pkg.created.slice(0, 10)}`}
      actions={
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          <Tag tone="ok">signed by Rezztor</Tag>
          <Tag tone="ok">fits this version</Tag>
          {pkg.restart === "application" && <Tag tone="warn">restart needed</Tag>}
        </div>
      }
    >
      {localError && <ScopeNote tone="crit">{localError}</ScopeNote>}
      <Row label="From, to" help="What you run, and what you will run.">
        {status.installed.version} → {pkg.version}
      </Row>
      <Row label="What changes" help="Written by Rezztor, inside the signed file.">
        <div style={{ textAlign: "left", maxWidth: "46ch" }}>
          {pkg.notes.length ? pkg.notes.map((note) => <div key={note}>{note}</div>) : "—"}
        </div>
      </Row>
      <Row label="Restart" help="Installing does not interrupt anybody. The restart does.">
        {pkg.restart === "application"
          ? "The application, about a minute. Not when only texts and screens are new for this installation."
          : "None"}
      </Row>
      <Row label="Database" help="Whether tables change.">
        {pkg.database_change
          ? "tables change · a dump is taken before the switch"
          : "no change · going back is automatic"}
      </Row>
      {pkg.os && (
        <Row label="Debian" help="Installed after the new version answers. Packages this installation has already are skipped.">
          {debianLine(pkg.os.packages.length, pkg.os.reboot)}
        </Row>
      )}
      <Row label="Signature" help="Checked again on the host before anything is installed.">
        <Mono>{pkg.key}</Mono> · valid
      </Row>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", padding: "var(--space-3) 0" }}>
        <Button variant="ghost" onClick={onDiscard} disabled={busy !== null}>
          {busy === "discard" ? "Discarding…" : "Discard"}
        </Button>
        <Button variant="primary" onClick={onInstall} disabled={busy !== null}>
          {busy === "install" ? "Starting…" : "Install"}
        </Button>
      </div>
    </Card>
  );
}

function InstallingView({ status }: { status: UpdateAvailable }) {
  const pkg = status.package;
  const steps = status.steps;
  const done = steps.filter((step) => step.state === "done").length;
  const total = steps.length;
  return (
    <Card
      title={pkg ? `Installing ${pkg.version}` : "Installing"}
      description="Everybody can keep working. Nothing changes for them until the restart."
      actions={
        total > 0 ? (
          <Tag tone="accent">
            step {Math.min(done + 1, total)} of {total}
          </Tag>
        ) : undefined
      }
    >
      <StepList steps={steps} />
    </Card>
  );
}

function StagedView({
  status,
  busy,
  localError,
  onRemove,
  onRestart,
}: {
  status: UpdateAvailable;
  busy: Busy;
  localError: string | null;
  onRemove: () => void;
  onRestart: () => void;
}) {
  const pkg = status.package!;
  return (
    <Card
      title="Restart to finish"
      description={`${pkg.version} is installed beside the running version. It starts at the next restart.`}
      actions={<Tag tone="warn">restart needed</Tag>}
    >
      {localError && <ScopeNote tone="crit">{localError}</ScopeNote>}
      <Row label="How long" help="The page comes back by itself.">
        about a minute
      </Row>
      <Row label="If it does not start" help="Nobody has to do anything.">
        the appliance returns to {status.installed.version}
      </Row>
      {pkg.os && (
        <Row label="Debian" help="Installed once the new version answers, while it runs.">
          {debianLine(pkg.os.packages.length, pkg.os.reboot)}
        </Row>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", padding: "var(--space-3) 0" }}>
        <Button variant="ghost" onClick={onRemove} disabled={busy !== null}>
          {busy === "discard" ? "Removing…" : "Remove this update"}
        </Button>
        <Button variant="primary" onClick={onRestart} disabled={busy !== null}>
          {busy === "restart" ? "Restarting…" : "Restart now"}
        </Button>
      </div>
    </Card>
  );
}

function RebootCard({ status, busy, onReboot }: { status: UpdateAvailable; busy: Busy; onReboot: () => void }) {
  const system = status.os!;
  const error = status.error && REBOOT_CODES.has(status.error.code) ? status.error : null;
  return (
    <Card
      title="Restart the appliance"
      description="Debian updates were installed that are in force only after a restart of the whole appliance."
      actions={<Tag tone="warn">restart needed</Tag>}
    >
      {error && <ScopeNote tone="crit">{error.text}</ScopeNote>}
      {system.applied > 0 && (
        <Row label="Debian" help={system.version ? `With update ${system.version}.` : "Installed by Debian itself."}>
          {system.applied} package{system.applied === 1 ? "" : "s"} updated
        </Row>
      )}
      <Row label="How long" help="The page comes back by itself.">
        a few minutes · agent runs in progress stop
      </Row>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", padding: "var(--space-3) 0" }}>
        <Button variant="primary" onClick={onReboot} disabled={busy !== null}>
          {busy === "reboot" ? "Restarting…" : "Restart the appliance"}
        </Button>
      </div>
    </Card>
  );
}

function RebootingView() {
  return (
    <div role="status" aria-live="polite" style={{ textAlign: "center", padding: "var(--space-6)", minHeight: "40vh" }}>
      <b style={{ fontSize: "var(--text-xl)", color: "var(--ink)" }}>The appliance is restarting</b>
      <p style={{ color: "var(--ink-2)", fontSize: "var(--text-md)" }}>
        This takes a few minutes. This page comes back by itself.
        <br />
        Do not close the virtual machine.
      </p>
    </div>
  );
}

function RestartingView({ version, debian = false }: { version: string | undefined; debian?: boolean }) {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        flex: 1,
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "var(--space-3)",
        minHeight: "40vh",
        textAlign: "center",
        padding: "var(--space-6)",
      }}
    >
      <div
        aria-hidden="true"
        style={{
          width: 28,
          height: 28,
          border: "3px solid var(--line)",
          borderTopColor: "var(--accent)",
          borderRadius: "50%",
          animation: "rezztor-spin 0.8s linear infinite",
        }}
      />
      <style>{`@keyframes rezztor-spin { to { transform: rotate(360deg); } }`}</style>
      <b style={{ fontSize: "var(--text-xl)", color: "var(--ink)" }}>
        {debian ? "Installing Debian updates" : "Rezztor EAgent is restarting"}
      </b>
      <span style={{ color: "var(--ink-2)", fontSize: "var(--text-md)", maxWidth: "42ch" }}>
        {debian
          ? `Version ${version ?? ""} runs. Debian's updates are being installed; this page comes back by itself.`
          : version
            ? `Starting version ${version}. This page comes back by itself.`
            : "This page comes back by itself."}
        <br />
        Do not close the virtual machine.
      </span>
    </div>
  );
}

function StepList({ steps }: { steps: UpdateStep[] }) {
  if (!steps.length) return null;
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: "var(--space-2) 0", display: "grid", gap: 8 }}>
      {steps.map((step) => {
        const muted = step.state === "waiting";
        const failed = step.state === "failed";
        const running = step.state === "running";
        return (
          <li
            key={step.id}
            style={{
              display: "flex",
              gap: "var(--space-2)",
              alignItems: "flex-start",
              color: failed ? "var(--crit)" : muted ? "var(--ink-3)" : running ? "var(--accent)" : "var(--ink)",
              fontSize: "var(--text-md)",
            }}
          >
            <span aria-hidden="true" style={{ width: "1.2em", flexShrink: 0 }}>
              {STEP_MARK[step.state]}
            </span>
            <span>{step.text}</span>
          </li>
        );
      })}
    </ul>
  );
}

function HistoryCard({ history }: { history: UpdateAvailable["history"] }) {
  return (
    <Card title="History" description="Every update tried on this installation.">
      <Table>
        <thead>
          <tr>
            <th>When</th>
            <th>Version</th>
            <th>By</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {history.map((row) => (
            <tr key={`${row.at}-${row.version}-${row.outcome}`}>
              <td>
                <DateTime value={row.at} />
              </td>
              <td>
                <Mono>{row.version}</Mono>
              </td>
              <td>{row.by}</td>
              <td>
                <Tag tone={row.outcome === "installed" ? "ok" : row.outcome === "rolled_back" ? "crit" : "muted"}>
                  {row.outcome}
                </Tag>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

function Bar({ percent, label }: { percent: number; label: string }) {
  return (
    <div
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      style={{
        marginTop: "var(--space-3)",
        height: 6,
        borderRadius: 3,
        background: "var(--line)",
        overflow: "hidden",
      }}
    >
      <div style={{ width: `${percent}%`, height: "100%", background: "var(--accent)" }} />
    </div>
  );
}

/** What the plan and the licence allow, in one sentence; null when the first update can be installed from here. */
function planNote(updates: UpdatesList): { tone: "warn" | "accent"; text: string } | null {
  if (updates.licence === "lapsed") {
    return {
      tone: "warn",
      text: "Renew to receive updates. The licence of this installation has lapsed, so updates follow the REZ-Free dates below.",
    };
  }
  if (updates.licence === "none") {
    return {
      tone: "warn",
      text: "Activate a licence to receive updates (Settings, Licence). Until then updates follow the REZ-Free dates below.",
    };
  }
  if (updates.plan === "free") {
    return {
      tone: "accent",
      text:
        "Upgrade to REZ-Pro to install from here. On REZ-Free each update can be downloaded from the customer portal " +
        "from its availability date (security fixes on release) and installed as a file below.",
    };
  }
  return null;
}

/** The host's last question to the update service went wrong: what it means, in plain words. */
function ServiceError({ code, text, updateKey }: { code: string; text: string; updateKey: string }) {
  if (code === "unknown_installation") {
    return (
      <ScopeNote tone="warn">
        Rezztor's update service does not know this installation yet. Register its update key once: sign in to the
        customer portal (portal.rezztor.com), open this installation under Installations, and paste the update key{" "}
        <Mono>{updateKey}</Mono> (shown above, with a Copy button). Then press Check now.
      </ScopeNote>
    );
  }
  if (code === "chain_gap") {
    return (
      <ScopeNote tone="warn">
        An update between the version you run and the ones listed is missing, so none can be installed from here:
        updates go in strictly in order. Ask Rezztor for the missing update and install it as a file below.
      </ScopeNote>
    );
  }
  if (code === "unreachable") {
    return (
      <ScopeNote tone="warn">
        Rezztor's update service could not be reached. This installation needs outbound HTTPS to
        it, through your proxy if you use one; the line below names the address it tried. Updates can still be
        installed as a file below.
        <br />
        <span style={{ color: "var(--ink-3)" }}>{text}</span>
      </ScopeNote>
    );
  }
  return <ScopeNote tone="warn">{serviceMessage({ code, text })}</ScopeNote>;
}

function AvailableCard({
  status,
  busy,
  pending,
  checking,
  error,
  onRefresh,
  onDownload,
  onUpdateAll,
  onDaily,
}: {
  status: UpdateAvailable;
  busy: Busy;
  pending: boolean;
  checking: boolean;
  error: string | null;
  onRefresh: () => void;
  onDownload: (version: string) => void;
  onUpdateAll: (count: number) => void;
  onDaily: (next: boolean) => void;
}) {
  const updates = status.updates ?? null;
  const listed = updates?.available ?? [];
  const note = updates ? planNote(updates) : null;
  const first = listed[0];
  const installable = Boolean(updates && !note && first?.downloadable && !updates.error);

  return (
    <Card
      title="Available updates"
      description="From Rezztor's update service, oldest first. They are installed in this order."
      actions={
        <Button onClick={onRefresh} disabled={busy !== null || pending}>
          {busy === "refresh" || checking ? "Checking…" : "Check now"}
        </Button>
      }
    >
      {error && <ScopeNote tone="crit">{error}</ScopeNote>}
      {!error && status.error && fromTheList(status.error) && (
        <ScopeNote tone="crit">{serviceMessage(status.error)}</ScopeNote>
      )}
      <Row label="Last checked" help="This installation asks; the list is what it heard then.">
        {updates?.checked_at ? <DateTime value={updates.checked_at} /> : <span>not yet</span>}
      </Row>
      <Row
        label="Check every day"
        help="Asks Rezztor's update service once a day. Switch off for a site that must not reach the internet; Check now and a file keep working."
      >
        <Toggle
          checked={status.daily_check !== false}
          onChange={onDaily}
          label="Check for updates every day"
          disabled={busy !== null}
        />
      </Row>
      {updates?.error && (
        <ServiceError code={updates.error.code} text={updates.error.text} updateKey={status.update_key} />
      )}
      {note && <ScopeNote tone={note.tone}>{note.text}</ScopeNote>}
      {updates && !updates.error && listed.length === 0 && (
        <ScopeNote>This installation runs the latest version.</ScopeNote>
      )}
      {listed.length > 0 && (
        <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: "var(--space-3)" }}>
          {listed.map((item, index) => (
            <ListedItem
              key={item.version}
              item={item}
              action={
                index === 0 && installable ? (
                  <Button variant="primary" onClick={() => onDownload(item.version)} disabled={busy !== null || pending}>
                    {busy === "download" ? "Starting…" : "Download and install"}
                  </Button>
                ) : null
              }
            />
          ))}
        </ol>
      )}
      {installable && listed.length > 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", padding: "var(--space-3) 0" }}>
          <Button onClick={() => onUpdateAll(listed.length)} disabled={busy !== null || pending}>
            {busy === "update_all" ? "Starting…" : `Update to latest (${listed.length})`}
          </Button>
        </div>
      )}
    </Card>
  );
}

function ListedItem({ item, action }: { item: ListedUpdate; action: ReactNode }) {
  return (
    <li
      style={{
        display: "grid",
        gap: "var(--space-2)",
        padding: "var(--space-3)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius)",
      }}
    >
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--space-2)" }}>
        <b style={{ color: "var(--ink)" }}>
          <Mono>{item.version}</Mono>
        </b>
        {item.security && <Tag tone="crit">security</Tag>}
        {item.bridge && <Tag tone="accent">new core release</Tag>}
        {item.os && item.os.packages > 0 && <Tag tone={item.os.reboot ? "warn" : "muted"}>{debianLine(item.os.packages, item.os.reboot)}</Tag>}
        <span style={{ color: "var(--ink-3)", fontSize: "var(--text-sm)" }}>
          released <DateTime value={item.released_at} mode="date" /> · {readableSize(item.size)} · available from{" "}
          <DateTime value={item.available_at} mode="date" />
        </span>
        {action && <span style={{ marginLeft: "auto" }}>{action}</span>}
      </div>
      {item.notes.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: "1.2em", color: "var(--ink-2)" }}>
          {item.notes.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

function DownloadProgress({ status }: { status: UpdateAvailable }) {
  const download = status.download;
  if (status.phase !== "downloading" || !download || download.size <= 0) return null;
  const percent = Math.min(100, Math.round((download.received / download.size) * 100));
  return (
    <>
      <Row label="Downloaded" help="A download that was interrupted goes on where it stopped.">
        {readableSize(download.received)} of {readableSize(download.size)}
      </Row>
      <Bar percent={percent} label={`Download of ${download.version}`} />
    </>
  );
}

function DownloadingView({ status }: { status: UpdateAvailable }) {
  const version = status.download?.version;
  return (
    <Card
      title={version ? `Downloading ${version}` : "Downloading"}
      description="Everybody can keep working. The file is checked like an uploaded one once it is here."
    >
      <DownloadProgress status={status} />
    </Card>
  );
}

function WalkingView({ status }: { status: UpdateAvailable }) {
  const progress = status.progress!;
  return (
    <Card
      title="Updating to the latest version"
      description="One update after another, in order. This page follows by itself; the application restarts in between."
      actions={
        <Tag tone="accent">
          {Math.min(progress.done + 1, progress.total)} of {progress.total}
        </Tag>
      }
    >
      <Row label="Now" help="The update being downloaded or installed.">
        <Mono>{progress.current}</Mono>
      </Row>
      <DownloadProgress status={status} />
      {status.phase === "installing" && <StepList steps={status.steps} />}
    </Card>
  );
}
