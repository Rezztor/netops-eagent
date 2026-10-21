/**
 * The conversation, as something you can read.
 *
 * A finished task routinely logs fifteen entries of which one is the answer.
 * Rendered flat and equal, the answer is just the last card and the reader has
 * to scroll past the working to find it. So the answer gets the room, and the
 * working folds into a group you can open when you want it.
 *
 * The grouping itself lives in lib/transcript.ts, which is pure and tested;
 * this file is only the markup.
 */

import { useEffect, useRef, useState } from "react";

import { attachmentOf, downloadAttachment, readableSize } from "@/api/attachments";
import { Markdown } from "@/lib/Markdown";
import { PERMISSIONS } from "@/api/types";
import { useCan } from "@/lib/session";
import {
  buildSections, failureText, findMatches, hasAttachment, interactionOfItem, isGroupClosed,
  isInteractionLive, isQuiet, sectionsWithMatches, stepLabel,
  type Entry, type RawEntry, type Section,
} from "@/lib/transcript";
import { InteractionCard } from "./InteractionCard";
import styles from "./chat.module.css";

/** How long an answer may be before it is worth folding. */
const ANSWER_CLAMP_CHARS = 1200;

function detailRows(entry: Entry): Array<[string, string]> {
  const kvps = entry.item.kvps;
  if (!kvps) return [];
  return Object.entries(kvps)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v, null, 2)] as [string, string]);
}

/**
 * A file the agent attached, as a control rather than a path.
 *
 * The whole point of the feature: agent code runs somewhere the user cannot
 * browse, so a path is a dead end and a button is not.
 */
function AttachmentButton({ kvps }: { kvps: Record<string, unknown> | null | undefined }) {
  const attachment = attachmentOf(kvps);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!attachment) return null;

  return (
    <button
      type="button"
      className={styles.attachment}
      disabled={busy}
      onClick={() => {
        setBusy(true);
        setFailed(false);
        void downloadAttachment(attachment)
          .catch(() => setFailed(true))
          .finally(() => setBusy(false));
      }}
    >
      <span aria-hidden="true">⭳</span>
      <span className={styles.attachmentName}>{attachment.name}</span>
      <span className={styles.attachmentSize}>
        {failed ? "could not download" : busy ? "…" : readableSize(attachment.bytes)}
      </span>
    </button>
  );
}

/** The values a step was logged with, minus the failure detail shown on its own. */
function TechnicalDetails({ rows, body }: { rows: Array<[string, string]>; body?: string }) {
  if (!rows.length && !body) return null;
  return (
    <details className={styles.stepTech}>
      <summary>Technical details</summary>
      {body && <pre className={styles.stepPre}>{body}</pre>}
      {rows.length > 0 && (
        <dl className={styles.stepDetails}>
          {rows.map(([key, value]) => (
            <div key={key} className={styles.stepDetail}>
              <dt>{key}</dt>
              <dd><pre className={styles.stepPre}>{value}</pre></dd>
            </div>
          ))}
        </dl>
      )}
    </details>
  );
}

function Step({ entry, onBranch, done }: { entry: Entry; onBranch: (no: number) => void; done: boolean }) {
  const [open, setOpen] = useState(false);
  // Tracebacks name server paths and provider URLs: administrators only.
  const isAdmin = useCan(PERMISSIONS.settingsManage);
  if (entry.kind !== "step") return null;
  const failed = entry.code === "ERR";
  const warned = entry.code === "WRN";
  const failure = failed || warned ? failureText(entry.item) : null;
  const details = detailRows(entry).filter(([key]) => !failure || key !== "details");
  const body = failure ? "" : String(entry.item.content ?? "");
  const attachment = attachmentOf(entry.item.kvps);
  const title = failure && !entry.item.heading ? failure.line || entry.title : entry.title;
  const mark = failed ? "✕" : warned ? "!" : done ? "✓" : "";
  const markLabel = failed ? "failed" : warned ? "warning" : done ? "done" : "running";

  return (
    <div className={styles.step}>
      <button
        type="button"
        className={styles.stepRow}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={styles.stepCaret} aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className={`${styles.stepMark} ${failed ? styles.stepMarkFailed : warned ? styles.stepMarkWarn : ""}`}>
          <span aria-hidden="true">{mark}</span>
          <span className="visually-hidden">{markLabel}</span>
        </span>
        <span className={`${styles.stepCode} ${styles[`code${entry.code}`] ?? ""}`}>{stepLabel(entry.code)}</span>
        <span className={styles.stepTitle}>{title || "—"}</span>
      </button>
      {attachment && (
        <div className={styles.stepAttachment}>
          <AttachmentButton kvps={entry.item.kvps} />
        </div>
      )}
      {open && (
        <div className={styles.stepBody}>
          {/* Repeated in full: the row's title is cut to one line, on a phone mid-sentence. */}
          {failure && failure.line && <p className={styles.stepLine}>{failure.line}</p>}
          {failure && isAdmin && failure.detail && (
            <details className={styles.stepTech}>
              <summary>Technical details</summary>
              <pre className={styles.stepPre}>{failure.detail}</pre>
            </details>
          )}
          {body && <pre className={styles.stepPre}>{body}</pre>}
          {(!failure || isAdmin) && <TechnicalDetails rows={details} />}
          <div className={styles.stepActions}>
            {body && (
              <button type="button" className={styles.entryAction}
                onClick={() => void navigator.clipboard?.writeText(body)}>Copy</button>
            )}
            <button type="button" className={styles.entryAction}
              title="Continue from here in a new conversation, leaving this one unchanged"
              onClick={() => onBranch(entry.item.no)}>Branch from here</button>
          </div>
        </div>
      )}
    </div>
  );
}

function StepGroup({
  group, closed, onBranch, forceOpen, activeNo,
}: {
  group: Extract<Section, { kind: "steps" }>;
  closed: boolean;
  onBranch: (no: number) => void;
  /** A find is running and this group holds a match, so it cannot stay folded. */
  forceOpen: boolean;
  activeNo: number | null;
}) {
  // A group of nothing but housekeeping is one quiet, folded line, live or not.
  const onlyQuiet = group.quiet > 0 && group.quiet === group.entries.length;
  const [open, setOpen] = useState(!closed && !onlyQuiet);
  const [showQuiet, setShowQuiet] = useState(false);
  // Quiet steps are hidden by default, but a match inside one has to be
  // reachable or the count lies about what can be found.
  const holdsActive = activeNo !== null && group.entries.some((e) => e.item.no === activeNo);
  const revealAll = showQuiet || (forceOpen && holdsActive);
  const shown = revealAll ? group.entries : group.entries.filter((e) => !isQuiet(e));
  const count = shown.length;
  const isOpen = open || forceOpen;

  return (
    <div className={onlyQuiet ? `${styles.group} ${styles.groupQuiet}` : styles.group}>
      <button
        type="button"
        className={styles.groupHeader}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={isOpen}
      >
        <span className={styles.stepCaret} aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
        <span className={styles.groupTitle}>{group.title}</span>
        <span className={styles.groupMeta}>
          {/*
            * "0 steps" is what a group of nothing but housekeeping used to
            * report, which reads as an empty group rather than a hidden one.
            * Count what is actually in there when none of it is shown.
            */}
          {count === 0 && group.quiet > 0
            ? `${group.quiet} housekeeping step${group.quiet === 1 ? "" : "s"}`
            : `${count} step${count === 1 ? "" : "s"}`}
          {/* Said on the header so a folded group cannot hide that something failed. */}
          {group.failed > 0 && <span className={styles.groupFailed}>{`, ${group.failed} failed`}</span>}
          {group.seconds !== null && group.seconds > 0 && ` · ${formatSeconds(group.seconds)}`}
        </span>
      </button>
      {isOpen && (
        <div className={styles.groupSteps}>
          {shown.map((entry, i) => (
            <Step
              key={`${entry.item.no}-${entry.item.id ?? ""}`}
              entry={entry}
              onBranch={onBranch}
              // Every step but the newest of a live group has finished.
              done={closed || i < shown.length - 1}
            />
          ))}
          {group.quiet > 0 && (
            // Said out loud rather than silently filtered: a transcript that
            // drops entries without saying so cannot be trusted to be complete.
            <button
              type="button"
              className={styles.quietToggle}
              onClick={() => setShowQuiet((v) => !v)}
            >
              {showQuiet
                ? `Hide ${group.quiet} housekeeping step${group.quiet === 1 ? "" : "s"}`
                : `Show ${group.quiet} housekeeping step${group.quiet === 1 ? "" : "s"}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function formatSeconds(total: number): string {
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return s ? `${m}m${s}s` : `${m}m`;
}

function Answer({ entry, onBranch }: { entry: Entry; onBranch: (no: number) => void }) {
  const text = entry.kind === "answer" ? entry.text : "";
  const long = text.length > ANSWER_CLAMP_CHARS;
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`${styles.entry} ${styles.entryAgent ?? ""}`}>
      <div className={styles.entryWho}>Agent</div>
      <div className={long && !expanded ? styles.answerClamped : undefined}>
        <Markdown text={text} />
      </div>
      {long && (
        <button type="button" className={styles.showMore} onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
      <div className={`${styles.entryActions} ${styles.entryActionsPinned}`}>
        {text && (
          <button type="button" className={styles.entryAction}
            onClick={() => void navigator.clipboard?.writeText(text)}>Copy</button>
        )}
        <button type="button" className={styles.entryAction}
          title="Continue from here in a new conversation, leaving this one unchanged"
          onClick={() => onBranch(entry.item.no)}>Branch from here</button>
      </div>
    </div>
  );
}

export function Transcript({
  items, onBranch, onAnswer, onCompose, find, onMatches,
}: {
  items: RawEntry[];
  onBranch: (no: number) => void;
  /** Answering a question card sends an ordinary message. */
  /** `answers` is the card's id, so the record ties the reply to its question. */
  onAnswer: (text: string, answers: string) => void;
  /** "Something else" puts the question in the composer rather than sending. */
  onCompose: (prefill: string) => void;
  /** An active find, or null. `activeNo` is the entry to bring into view. */
  find?: { term: string; activeNo: number | null } | null;
  /** Reports what the find turned up, so the bar can count it. */
  onMatches?: (matches: ReturnType<typeof findMatches>) => void;
}) {
  const sections = buildSections(items);
  const term = find?.term ?? "";
  const activeNo = find?.activeNo ?? null;
  const matches = term ? findMatches(sections, term) : [];
  const openSections = sectionsWithMatches(matches);
  const container = useRef<HTMLDivElement | null>(null);

  // Reported through an effect rather than during render: calling a parent's
  // setState while rendering is what React warns about, and the warning is
  // right -- it is a second render pass hiding as a callback.
  const signature = matches.map((m) => m.no).join(",");
  useEffect(() => {
    onMatches?.(matches);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  useEffect(() => {
    if (activeNo === null) return;
    container.current
      ?.querySelector(`[data-find-no="${activeNo}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeNo]);

  return (
    <div ref={container}>
      {sections.map((section, index) => {
        if (section.kind === "steps") {
          return (
            <StepGroup
              key={`g${section.entries[0]?.item.no ?? index}`}
              group={section}
              closed={isGroupClosed(sections, index)}
              onBranch={onBranch}
              forceOpen={openSections.has(index)}
              activeNo={activeNo}
            />
          );
        }
        const entry = section.entry;
        const key = `${entry.item.no}-${entry.item.id ?? ""}`;
        const hit = activeNo === entry.item.no ? styles.findHit : "";
        if (entry.kind === "answer") {
          return (
            <div key={key} data-find-no={entry.item.no} className={hit}>
              <Answer entry={entry} onBranch={onBranch} />
            </div>
          );
        }
        const interaction = interactionOfItem(entry.item);
        if (interaction) {
          return (
            <div key={key} data-find-no={entry.item.no} className={hit}>
            <InteractionCard
              interaction={interaction}
              live={isInteractionLive(sections, index)}
              context={String(entry.item.content ?? "")}
              onAnswer={onAnswer}
              onCompose={onCompose}
            />
            </div>
          );
        }
        if (hasAttachment(entry)) {
          return (
            <div key={key} data-find-no={entry.item.no} className={`${styles.entry} ${hit}`}>
              <div className={styles.entryContent}>{entry.item.content || entry.title}</div>
              <AttachmentButton kvps={entry.item.kvps} />
            </div>
          );
        }
        return (
          <div
            key={key}
            data-find-no={entry.item.no}
            className={`${styles.entry} ${styles.entryUser ?? ""} ${hit}`}
          >
            <div className={styles.entryWho}>You</div>
            <div className={styles.entryContent}>{entry.item.content || entry.title || "—"}</div>
          </div>
        );
      })}
    </div>
  );
}
