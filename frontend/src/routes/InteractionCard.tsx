/**
 * A question the agent asked, rendered as answers you can click.
 *
 * The card stands outside the collapsible step group -- `buildSections` gives it
 * a section of its own -- because it is the one thing in the turn the user is
 * meant to act on, and a control that folds away is a control nobody finds.
 *
 * It goes quiet once the conversation moves past it. `isInteractionLive` decides
 * that from what came afterwards, so a question twenty messages up is readable
 * but not clickable: an answer to something the agent stopped asking looks like
 * it worked, which is the worst way for this to fail.
 *
 * Approvals render through the same component and the same transport. What
 * differs is weight: they get two inline buttons coloured by the tone the card
 * carries, and the consequence sits directly under the question, because the
 * consequence is the thing being said yes to.
 *
 * A third kind, `decision`, is the one that does authorise something. A question
 * and an approval both send a chat message, which the server cannot tell apart
 * from the user typing the same word -- fine for the agent checking before doing
 * something it may already do, and worthless as evidence. A decision instead
 * calls a named endpoint that checks the permission server-side, so an engineer
 * approving the exact commands a change will push leaves a record that means
 * something. The endpoint and the payloads come from the card; this file never
 * builds either, because a client that constructed its own payload would be a
 * second place the decision's meaning is decided.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { apiDownload, apiRequest } from "@/api/client";
import { useConfirm } from "@/components";
import { saveCredential } from "@/api/networkConsole";
import { PERMISSIONS } from "@/api/types";
import { matchesReference } from "@/lib/referencePattern";
import { useCan } from "@/lib/session";
import { DOCUMENT_ENDPOINT, interactionOf, type Interaction } from "@/lib/transcript";
import styles from "./chat.module.css";

type CardProps = {
  interaction: Interaction;
  live: boolean;
  context: string;
  onAnswer: (text: string, answers: string) => void;
  onCompose: (prefill: string) => void;
};

/** The time alone when it is today; the date too otherwise (an Azure change waits up to 30 days for its number). */
function expiryText(seconds: number): string {
  const when = new Date(seconds * 1000);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const today = when.toDateString() === new Date().toDateString();
  return today ? time : `${when.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" })}, ${time}`;
}

const APPROVE_ENDPOINT = "plugins/_ea_azure_change/azure_approve";
// Where each approval endpoint's change can be read back. A device card had no such read once, so a card that
// was already approved or used showed live buttons again after a reload.
const STATE_ENDPOINTS: Record<string, string> = {
  [APPROVE_ENDPOINT]: "plugins/_ea_azure_change/azure_changes",
  "plugins/_ea_ansible/change_approve": "plugins/_ea_ansible/changes_list",
};

/** What the server says about a change (one `change_id`); the card draws what it is told. */
type ChangeState = {
  status: string;
  /** "read" for a device read: it is finished, not applied. */
  kind?: string;
  downloaded?: boolean;
  reference: string;
  approved_by_name?: string;
  approved_at?: number | null;
  /** A change set only: where each step stands. The record is the truth; the card draws it. */
  steps?: { n: number; title: string; status: string }[];
};

const when = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** What a step chip says. A step that is only planned gets none. */
const STEP_STATES: Record<string, string> = {
  applying: "applying",
  applied: "applied",
  verify_failed: "verify failed",
  failed: "failed",
  superseded: "plan changed",
  not_run: "not run",
};
const STOPPED = ["failed", "verify_failed", "superseded"];

/** "Applied 2 of 6. Stopped at step 3." once a set has begun, or "" (nothing ran, or not a set). */
function setProgress(state: ChangeState): string {
  const steps = state.steps ?? [];
  if (!steps.some((s) => s.status !== "planned")) return "";
  const stopped = steps.find((s) => STOPPED.includes(s.status));
  return `Applied ${steps.filter((s) => s.status === "applied").length} of ${steps.length}.${stopped ? ` Stopped at step ${stopped.n}.` : ""}`;
}

/** The sentence for a change that is no longer awaiting approval, or "" while it still is. */
function outcomeText(state: ChangeState): string {
  const number = state.reference ? ` with ${state.reference}` : "";
  const by = state.approved_by_name ? ` by ${state.approved_by_name}` : "";
  const at = state.approved_at ? `, ${when(state.approved_at)}` : "";
  const progress = setProgress(state);
  if (progress && state.status !== "applying") {
    return state.status === "applied" || state.status === "closed"
      ? `Applied all ${state.steps!.length} steps. Approved${number}${by}${at}.`
      : `${progress} Approved${number}${by}${at}.`;
  }
  switch (state.status) {
    case "awaiting_approval":
      return "";
    case "approved":
      return `Approved${number}${by}${at}.`;
    case "applying":
      return `Approved${number}${by}${at}. Applying now.${progress ? ` ${progress}` : ""}`;
    case "applied":
    case "closed":
      return `${state.kind === "read" ? "Read finished" : "Applied"}. Approved${number}${by}${at}.`;
    case "verify_failed":
      return `Applied, but the check afterwards did not find what was intended. Approved${number}${by}${at}.`;
    case "failed":
      return `Did not run. Approved${number}${by}${at}.`;
    case "rejected":
      return "Rejected. Nothing was changed.";
    case "superseded":
      return "Superseded: a newer plan was made for this change, so this card and its document are void. Use the newest card.";
    case "cancelled":
      return "Cancelled. Nothing was changed.";
    case "expired":
      return "Expired with its document. Ask for a fresh draft.";
    default:
      return `This change is ${state.status.replace(/_/g, " ")}.`;
  }
}

/**
 * A fourth kind, `secret_form`, collects a device secret without it ever being
 * chat. The browser posts it straight to the network_inventory endpoint; the
 * agent only learns "Credential <name> stored.", which is the message sent on
 * success. The value lives in an uncontrolled input -- never in component state,
 * a chat message or a log -- and the input is emptied the moment it is read.
 */
export function InteractionCard(props: CardProps) {
  const form = props.interaction.secretForm;
  if (form) {
    return <SecretFormCard interaction={props.interaction} form={form} live={props.live} onAnswer={props.onAnswer} />;
  }
  return <ChoiceCard {...props} />;
}

function SecretFormCard({
  interaction,
  form,
  live,
  onAnswer,
}: {
  interaction: Interaction;
  form: NonNullable<Interaction["secretForm"]>;
  live: boolean;
  onAnswer: (text: string, answers: string) => void;
}) {
  const allowed = useCan(PERMISSIONS.pluginsManage);
  const secretRef = useRef<HTMLInputElement>(null);
  const userRef = useRef<HTMLInputElement>(null);
  const [working, setWorking] = useState(false);
  const [failure, setFailure] = useState("");
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const done = fingerprint !== null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const input = secretRef.current;
    const secret = input?.value ?? "";
    if (!input || !secret || working || done) return;
    input.value = "";
    setWorking(true);
    setFailure("");
    try {
      const result = await saveCredential({
        name: form.name,
        kind: form.kind,
        secret,
        username: userRef.current?.value.trim() ?? "",
      });
      setFingerprint(result.credential?.fingerprint ?? "");
      onAnswer(`Credential ${form.name} stored.`, interaction.id);
    } catch (problem) {
      setFailure(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setWorking(false);
    }
  }

  const open = live && !done;
  return (
    <div className={open ? styles.card : `${styles.card} ${styles.cardSpent}`}>
      <p className={styles.cardQuestion}>{interaction.question}</p>
      {form.note && <p className={styles.cardContext}>{form.note}</p>}
      {!allowed ? (
        <p className={styles.cardContext}>
          Storing a credential is for administrators. Ask an administrator to store{" "}
          <b>{form.name}</b>.
        </p>
      ) : (
        <form className={styles.cardSecret} onSubmit={(e) => void submit(e)} autoComplete="off">
          <label>
            Credential
            <input type="text" value={form.name} readOnly />
          </label>
          <label>
            Username (optional)
            <input type="text" ref={userRef} defaultValue={form.username} autoComplete="off" disabled={!open || working} />
          </label>
          <label>
            Secret
            <input type="password" ref={secretRef} autoComplete="new-password" disabled={!open || working} />
          </label>
          <button type="submit" className={styles.entryAction} disabled={!open || working}>
            {working ? "Saving…" : "Save"}
          </button>
        </form>
      )}
      {done && (
        <p className={styles.cardContext}>
          Stored.{fingerprint ? ` Fingerprint ${fingerprint}.` : ""} The value is not shown again.
        </p>
      )}
      {failure && <p className={styles.cardStale} role="alert">{failure}</p>}
      {!live && !done && <p className={styles.cardStale}>Answered, or the conversation moved on.</p>}
    </div>
  );
}

const TIER_LABELS: Record<string, string> = {
  read: "Read only",
  "change-low": "Change · low risk",
  "change-high": "Change · high risk",
  suggestion: "Suggestion",
};

const isRecordLike = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object";

/** Plan lines coloured by their first character: `+` makes, `-` removes, `~` changes in place. */
function DiffLines({ lines }: { lines: string[] }) {
  return (
    <>
      {lines.map((line, i) => (
        <span
          key={i}
          className={line.startsWith("+") ? styles.diffAdd : line.startsWith("-") ? styles.diffDel : line.startsWith("~") ? styles.diffUpd : undefined}
        >
          {line + "\n"}
        </span>
      ))}
    </>
  );
}

function ChoiceCard({
  interaction: original,
  live,
  context,
  onAnswer,
  onCompose,
}: CardProps) {
  const [picked, setPicked] = useState<string[]>([]);
  const [sent, setSent] = useState(false);
  const [failure, setFailure] = useState("");
  const [working, setWorking] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  // An edit replaces the card with the one the server rebuilt: new commands, new
  // hash in the payloads, fresh expiry. Nothing here computes any of them.
  const [rebuilt, setRebuilt] = useState<Interaction | null>(null);
  const interaction = rebuilt ?? original;
  const [draft, setDraft] = useState<string | null>(null);
  // change-high: Approve stays disabled until this matches `confirm`, and the typed text is
  // the one field added to the approve payload the card was given.
  const [typed, setTyped] = useState("");
  // An Azure change: Approve stays disabled until this matches the card's pattern, and the value travels as `reference`.
  const [number, setNumber] = useState("");
  const [fetched, setFetched] = useState<string[]>([]);
  // The change's own state, from the server: it survives a reload and a second browser, which this card's local
  // `fetched` and `sent` do not. Absent (never fetched, or the request failed) the card behaves on what it knows.
  const [server, setServer] = useState<ChangeState | null>(null);

  const isDecision = interaction.kind === "decision";
  // Every decision endpoint requires network.approve, for Reject as well as
  // Approve. Without it the buttons would only ever earn a refusal after the
  // click, so they are off and say why. The endpoint still decides.
  const canDecide = useCan(PERMISSIONS.networkApprove) || !isDecision;
  const { confirm } = useConfirm();
  const expiresAt = interaction.expiresAt;
  // Expiry is the server's clock deciding; this only stops offering a button the
  // endpoint would refuse. A card past it goes quiet like one the chat moved past.
  const expired = expiresAt !== undefined && now >= expiresAt * 1000;
  useEffect(() => {
    if (expiresAt === undefined || expired) return;
    const wait = Math.min(expiresAt * 1000 - Date.now(), 2 ** 31 - 1);
    const timer = setTimeout(() => setNow(Date.now()), wait);
    return () => clearTimeout(timer);
  }, [expiresAt, expired]);
  const first = interaction.payloads?.[0];
  const stateEndpoint = STATE_ENDPOINTS[interaction.endpoint ?? ""] ?? "";
  const changeId = stateEndpoint && isRecordLike(first) ? String(first.change_id ?? "") : "";
  const awaiting = !server || server.status === "awaiting_approval";
  const running = server?.status === "approved" || server?.status === "applying";
  const outcome = server ? outcomeText(server) : "";
  const refresh = useCallback(async () => {
    if (!changeId) return;
    try {
      const result = await apiRequest<{ ok?: boolean; change?: ChangeState }>(stateEndpoint, { body: { change_id: changeId } });
      if (result?.ok && result.change) setServer(result.change);
    } catch {
      // The card still works on what it knows; the endpoint decides every approval anyway.
    }
  }, [changeId, stateEndpoint]);
  useEffect(() => {
    if (!changeId) return;
    void refresh();
    // Another card of this chat can supersede this change; a card on screen notices without a reload. And an
    // approved or applying change is watched until it ends, so a set's step chips turn as the steps finish.
    if (!(live && awaiting) && !running) return;
    const timer = setInterval(() => document.visibilityState === "visible" && void refresh(), 10_000);
    return () => clearInterval(timer);
  }, [changeId, live, awaiting, running, refresh]);
  const answerable = live && !sent && !working && !expired && awaiting && canDecide;
  // Azure: the order is on the card (1 download, 2 number, 3 approve), and Approve waits for step 1.
  const steps = interaction.reference !== undefined && Boolean(interaction.documents?.length);
  const downloaded = Boolean(server?.downloaded) || fetched.length > 0;
  const approveBlock =
    !steps || !answerable || outcome
      ? ""
      : !downloaded
        ? "Approve is off until you download the change document (step 1)."
        : !matchesReference(interaction.reference!.pattern, number)
          ? "Approve is off until the number matches the format (step 2)."
          : "";
  const editing = draft !== null;
  // Why Approve is off, whatever the reason; a disabled button that does not say why reads as broken.
  const approveWhy =
    live && !sent && !expired && awaiting && !outcome && isDecision && !canDecide
      ? "Deciding this needs an engineer with approval rights. Ask one to approve or reject it."
      : approveBlock ||
        (answerable && editing
          ? "Save or cancel the command edit first."
          : answerable && interaction.confirm !== undefined && typed.trim() !== interaction.confirm
            ? `Approve is off until you type ${interaction.confirm} above.`
            : "");
  // A decision is laid out like an approval -- two inline buttons, toned -- and
  // behaves like neither.
  const isApproval = interaction.kind === "approval" || isDecision;

  async function decide(index: number, label: string) {
    const endpoint = interaction.endpoint;
    const payload = interaction.payloads?.[index];
    if (!endpoint || payload === undefined) return;
    const approving = interaction.options[index]?.tone === "go";
    const confirming = interaction.confirm !== undefined && approving;
    const referencing = interaction.reference !== undefined && approving;
    if (
      !approving &&
      !(await confirm({
        title: `${label} this change?`,
        body: "Nothing is changed on the device, and this card cannot be used again.",
        confirmLabel: label,
        danger: true,
      }))
    ) {
      return;
    }
    setWorking(true);
    setFailure("");
    try {
      const result = await apiRequest<{ ok?: boolean; error?: string }>(
        endpoint,
        {
          body: {
            ...(payload as Record<string, unknown>),
            ...(confirming ? { confirmation: typed.trim() } : {}),
            ...(referencing ? { reference: number.trim() } : {}),
          },
        },
      );
      if (result && result.ok === false) {
        // The endpoint's own refusal, shown as it was written. The usual reason
        // is the one worth reading: the device moved, so the commands that would
        // run are no longer the commands on screen.
        setFailure(result.error || "That decision was not recorded.");
        return;
      }
      setSent(true);
      // Said in the conversation as well as recorded, so the transcript shows
      // what happened rather than going quiet at the moment that matters. Said
      // as what it is: a bare "Approve" reads to the agent like the user typing
      // the word, which it rightly refuses to treat as an approval (seen on the
      // first real Azure change).
      const reference = referencing && number.trim() ? ` with ${number.trim()}` : "";
      const said = approving ? `Approved on the card${reference}.` : `${label === "Reject" ? "Rejected" : label} on the card.`;
      onAnswer(said, interaction.id);
      void refresh();
    } catch (problem) {
      setFailure(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setWorking(false);
    }
  }

  async function fetchDocument(label: string, payload: Record<string, unknown>) {
    setWorking(true);
    setFailure("");
    try {
      const name = `azure-${String(payload.version ?? "change")}-${String(payload.change_id ?? "").slice(0, 8)}.${String(payload.format ?? "pdf")}`;
      await apiDownload(DOCUMENT_ENDPOINT, { method: "POST", body: payload, filename: name });
      setFetched((current) => [...current, label]);
      void refresh();
    } catch (problem) {
      setFailure(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setWorking(false);
    }
  }

  async function saveEdit() {
    const edit = interaction.edit;
    if (!edit || draft === null) return;
    setWorking(true);
    setFailure("");
    try {
      const result = await apiRequest<{ ok?: boolean; error?: string; card?: Record<string, unknown> }>(edit.endpoint, {
        body: { ...edit.payload, commands: draft.split("\n").map((l) => l.trim()).filter(Boolean) },
      });
      const next = result?.ok === false ? null : interactionOf(result?.card);
      if (!next) {
        setFailure(result?.error || "That edit was not recorded.");
        return;
      }
      setRebuilt(next);
      setServer(null);
      setDraft(null);
      // A new plan is a new document: the old one is void.
      setFetched([]);
    } catch (problem) {
      setFailure(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setWorking(false);
    }
  }

  function choose(label: string, index: number) {
    if (!answerable) return;
    if (isDecision) {
      void decide(index, label);
      return;
    }
    if (!interaction.multi) {
      setSent(true);
      onAnswer(label, interaction.id);
      return;
    }
    setPicked((current) =>
      current.includes(label) ? current.filter((l) => l !== label) : [...current, label],
    );
  }

  function submitMulti() {
    if (!answerable || picked.length === 0) return;
    setSent(true);
    onAnswer(picked.join(", "), interaction.id);
  }

  return (
    <div className={answerable ? styles.card : `${styles.card} ${styles.cardSpent}`}>
      <p className={styles.cardQuestion}>{interaction.question}</p>
      {(interaction.consequence || context) && (
        <p className={styles.cardContext}>{interaction.consequence || context}</p>
      )}

      {interaction.tier && (
        <span className={styles.cardTier}>{TIER_LABELS[interaction.tier] ?? interaction.tier}</span>
      )}
      {interaction.tier === "suggestion" && (
        <p className={styles.cardContext}>
          The product does not do this. The steps are for an engineer to follow by hand, outside the product.
        </p>
      )}
      {interaction.details && (
        <dl className={styles.cardDetails}>
          {interaction.details.map((row) => (
            <div key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {interaction.checks && (
        <ul className={styles.cardChecks}>
          {interaction.checks.map((check) => (
            <li
              key={check.label}
              className={check.level === "warn" ? styles.cardCheckWarn : check.ok ? styles.cardCheckOk : styles.cardCheckFail}
              title={check.detail}
            >
              {check.level === "warn" ? "!" : check.ok ? "✓" : "✗"} {check.label}
              {check.detail && (!check.ok || check.level === "warn") ? ` — ${check.detail}` : ""}
            </li>
          ))}
        </ul>
      )}
      {interaction.steps && (
        <div className={styles.cardSteps}>
          {interaction.steps.map((step) => {
            const state = server?.steps?.find((s) => s.n === step.n)?.status ?? "";
            return (
              <details key={step.n} className={styles.cardSetStep}>
                <summary>
                  {step.n}. {step.title}
                  {step.adds !== undefined ? ` · adds ${step.adds}` : ""}
                  {STEP_STATES[state] && (
                    <span className={`${styles.cardStepState} ${styles[`cardStepState_${state}`] ?? ""}`}>{STEP_STATES[state]}</span>
                  )}
                </summary>
                <pre className={styles.cardCommands} aria-label={`Step ${step.n} plan`}>
                  <DiffLines lines={step.lines} />
                </pre>
              </details>
            );
          })}
        </div>
      )}
      {interaction.commands && draft === null && (
        <pre className={styles.cardCommands}>{interaction.commands.join("\n")}</pre>
      )}
      {interaction.code && (
        <details>
          <summary>Code ({interaction.code.split("\n").length} lines)</summary>
          <pre className={styles.cardCommands} aria-label="Code">{interaction.code}</pre>
        </details>
      )}
      {interaction.diff && (
        <pre className={styles.cardCommands} aria-label="Diff">
          <DiffLines lines={interaction.diff} />
        </pre>
      )}
      {interaction.rollback && (
        <>
          <p className={styles.cardContext}>Rollback</p>
          <pre className={styles.cardCommands} aria-label="Rollback">{interaction.rollback.join("\n")}</pre>
        </>
      )}
      {draft !== null && (
        <>
          <textarea
            className={styles.cardEdit}
            aria-label="Commands to run"
            value={draft}
            rows={Math.max(3, draft.split("\n").length + 1)}
            spellCheck={false}
            disabled={working}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className={styles.cardActions}>
            <button type="button" className={styles.entryAction} disabled={working || !draft.trim()} onClick={() => void saveEdit()}>
              Save commands
            </button>
            <button type="button" className={styles.entryAction} disabled={working} onClick={() => setDraft(null)}>
              Cancel
            </button>
          </div>
        </>
      )}
      {interaction.edit && interaction.commands && draft === null && answerable && (
        <div className={styles.cardActions}>
          <button
            type="button"
            className={styles.entryAction}
            onClick={() => setDraft(interaction.commands!.filter((l) => !l.startsWith("#")).join("\n"))}
          >
            Edit commands
          </button>
        </div>
      )}
      {expiresAt !== undefined && !outcome && (
        <p className={styles.cardContext}>
          {expired
            ? "Expired. Ask again for a fresh approval."
            : `Expires ${expiryText(expiresAt)}`}
        </p>
      )}

      {/* Announced: the outcome arrives from the server some seconds after the click. */}
      <div role="status">{outcome && <p className={styles.cardOutcome}>{outcome}</p>}</div>

      {steps && !outcome && (
        <p className={styles.cardStep}>
          <span>1</span> Download the change document{downloaded ? " ✓" : ""}
        </p>
      )}
      {interaction.documents && !outcome && (
        <div className={styles.cardActions}>
          {interaction.documents.map((doc) => (
            <button
              key={doc.label}
              type="button"
              className={styles.cardDocument}
              disabled={working || editing}
              onClick={() => void fetchDocument(doc.label, doc.payload)}
            >
              {fetched.includes(doc.label) ? `${doc.label} · downloaded` : doc.label}
            </button>
          ))}
        </div>
      )}

      {steps && !outcome && (
        <p className={styles.cardStep}>
          <span>2</span> Enter the CR or SR number
        </p>
      )}
      {interaction.reference && !outcome && (
        <label className={styles.cardConfirm}>
          {interaction.reference.label}
          {interaction.reference.example ? ` (for example ${interaction.reference.example})` : ""}
          <input
            type="text"
            value={number}
            maxLength={40}
            disabled={!answerable}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setNumber(e.target.value)}
          />
        </label>
      )}

      {interaction.confirm !== undefined && (
        <label className={styles.cardConfirm}>
          Type <code>{interaction.confirm}</code> to approve
          <input
            type="text"
            value={typed}
            disabled={!answerable}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setTyped(e.target.value)}
          />
        </label>
      )}

      {steps && !outcome && (
        <p className={styles.cardStep}>
          <span>3</span> Press Approve
        </p>
      )}
      {!outcome && (
      <div className={isApproval ? styles.cardDecisions : styles.cardOptions}>
        {interaction.options.map((option, index) => {
          const on = picked.includes(option.label);
          const tone =
            option.tone === "go"
              ? styles.cardGo
              : option.tone === "stop"
                ? styles.cardStop
                : "";
          const base = isApproval ? `${styles.cardDecision} ${tone}` : styles.cardOption;
          return (
            <button
              key={option.label}
              type="button"
              className={on ? `${base} ${styles.cardOptionOn}` : base}
              disabled={
                !answerable ||
                editing ||
                (interaction.confirm !== undefined && option.tone === "go" && typed.trim() !== interaction.confirm) ||
                (steps && option.tone === "go" && !downloaded) ||
                (interaction.reference !== undefined && option.tone === "go" && !matchesReference(interaction.reference.pattern, number))
              }
              aria-pressed={interaction.multi ? on : undefined}
              onClick={() => choose(option.label, index)}
            >
              <b>{option.label}</b>
              {option.detail && <span>{option.detail}</span>}
            </button>
          );
        })}

        {approveWhy && <span className={styles.cardWhy}>{approveWhy}</span>}

        {interaction.allowOther && (
          <button
            type="button"
            className={
              isApproval
                ? `${styles.cardDecision} ${styles.cardOther}`
                : `${styles.cardOption} ${styles.cardOther}`
            }
            disabled={!answerable}
            // Quoting the question into the composer keeps a typed answer an
            // answer to *this*, rather than a new topic the agent has to guess
            // the relevance of.
            onClick={() => onCompose(`Re: ${interaction.question}\n\n`)}
          >
            <b>Something else…</b>
            {!isApproval && <span>type your own answer</span>}
          </button>
        )}
      </div>

      )}

      {interaction.multi && answerable && (
        <div className={styles.cardActions}>
          <button
            type="button"
            className={styles.entryAction}
            disabled={picked.length === 0}
            onClick={submitMulti}
          >
            {picked.length === 0 ? "Pick one or more" : `Send ${picked.length} selected`}
          </button>
        </div>
      )}

      {failure && <p className={styles.cardStale} role="alert">{failure}</p>}

      {!live && !expired && !outcome && (
        <p className={styles.cardStale}>
          {isDecision
            ? "Decided, or withdrawn. A decision that was never recorded is not an approval."
            : isApproval
              ? "Decided, or the conversation moved on. Silence was not approval."
              : "Answered, or the conversation moved on."}
        </p>
      )}
    </div>
  );
}
