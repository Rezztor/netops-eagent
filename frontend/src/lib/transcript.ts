/**
 * Turning a flat log into a readable transcript.
 *
 * The backend emits one log item per thing that happened -- a completed task
 * routinely produces fifteen or more, of which one is the answer. Rendering them
 * as equals buries the answer in its own working.
 *
 * So there are two kinds of entry. The root agent's response is the answer, and
 * gets the room. Everything else is a step: one line, a code, expandable. Steps
 * between answers collect into a group that can be folded away once its answer
 * has arrived.
 *
 * Pure functions over data, so the grammar is testable without a DOM.
 */

export interface RawEntry {
  no: number;
  id?: string;
  type: string;
  heading?: string;
  content?: string;
  kvps?: Record<string, unknown> | null;
  timestamp?: string;
  agentno?: number;
}

/** Three letters, because the column has to stay narrow enough to scan. */
const CODES: Record<string, string> = {
  agent: "GEN",
  code_exe: "EXE",
  browser: "WEB",
  subagent: "SUB",
  tool: "TLL",
  mcp: "MCP",
  util: "UTL",
  info: "INF",
  hint: "INF",
  progress: "INF",
  warning: "WRN",
  error: "ERR",
  input: "INP",
  response: "RES",
};

export function stepCode(type: string): string {
  return CODES[(type || "").toLowerCase()] ?? "LOG";
}

/**
 * What a step is, in a word a reader knows. The codes above stay as the
 * colour key; this is what is printed.
 */
const LABELS: Record<string, string> = {
  GEN: "Thinking",
  EXE: "Ran code",
  WEB: "Browsed",
  SUB: "Delegated",
  TLL: "Tool",
  MCP: "Tool",
  UTL: "Housekeeping",
  INF: "Note",
  WRN: "Warning",
  ERR: "Failed",
  INP: "Input",
  RES: "Reply",
  LOG: "Step",
};

export function stepLabel(code: string): string {
  return LABELS[code] ?? "Step";
}

/** A step that reports something going wrong. */
export function isFailure(entry: Entry): boolean {
  return entry.kind === "step" && entry.code === "ERR";
}

const TRACEBACK = /Traceback \(most recent call last\)|\n\s+File "/;
export const FAILURE_FALLBACK =
  "Something went wrong while the agent was working. Try again; if it repeats, ask an administrator.";

/**
 * What a failed or warning step says to a reader, and what it says to an
 * administrator.
 *
 * The server writes one sentence and puts the traceback in `kvps.details`
 * (helpers/errors.py log_failure). Conversations recorded before that carry
 * the traceback in the content itself, server paths and all, so a content that
 * looks like one is replaced by the generic sentence and becomes the detail.
 */
export function failureText(item: RawEntry): { line: string; detail: string } {
  const content = String(item.content ?? "");
  const detail = typeof item.kvps?.details === "string" ? item.kvps.details : "";
  if (TRACEBACK.test(content)) return { line: FAILURE_FALLBACK, detail: detail || content };
  return { line: content.split("\n")[0]?.trim() ?? "", detail };
}

/**
 * The backend puts `icon://name` markers in headings for the interface to swap
 * for glyphs. Nothing here does, and left alone they render as literal text --
 * "icon://terminal [0] total 0 icon://done_all".
 */
export function stripIconMarkers(text: string): string {
  return String(text ?? "").replace(/icon:\/\/[\w-]+/g, "").replace(/\s{2,}/g, " ").trim();
}

export type Entry =
  | { kind: "user"; item: RawEntry; title: string }
  | { kind: "answer"; item: RawEntry; text: string }
  | { kind: "step"; item: RawEntry; code: string; title: string };

/**
 * Types whose steps are housekeeping rather than work.
 *
 * The memory plugin narrates every recall -- "Preloading knowledge...",
 * "2 memories and 0 relevant solutions found" -- once or twice a turn. It says
 * nothing about what the agent is doing and crowds out what does.
 *
 * Hidden, not dropped: they stay in the group, stay counted, and one click
 * brings them back. A transcript that quietly discards entries is worse than a
 * noisy one, because you cannot tell it is doing it.
 */
const QUIET_TYPES = new Set(["util"]);

export function isQuiet(entry: Entry): boolean {
  return entry.kind === "step" && QUIET_TYPES.has((entry.item.type || "").toLowerCase());
}

/**
 * Whether this entry carries a file for the user to download.
 *
 * It matters to the grouping, not just the rendering. An attachment logs as an
 * ordinary step, and steps fold away once the answer arrives -- so the one thing
 * the user is meant to click would end up behind a disclosure triangle. Entries
 * carrying a file stand outside the group instead.
 */
export function hasAttachment(entry: Entry): boolean {
  const kvps = entry.item.kvps;
  return Boolean(kvps && kvps.attachment_id && kvps.attachment_name);
}

/**
 * A question the agent asked, as something to click rather than something to
 * read and reply to.
 *
 * Detected from `kvps`, the same channel an attachment travels on. The options
 * arrive as one JSON value rather than as numbered keys, so a malformed card is
 * obvious here instead of rendering half a question.
 */
export type InteractionTone = "go" | "stop" | "neutral";

export interface InteractionOption {
  label: string;
  detail?: string;
  /** How the decision reads. Carried on the card so the interface never has to
   *  infer intent from the word "approve", which holds until someone writes
   *  "Go ahead" -- exactly when getting it wrong matters most. */
  tone?: InteractionTone;
}

/** Where a secret form submits. Fixed here as well as in the card (cards.py):
 *  a card naming any other endpoint is not rendered, so nothing a log item says
 *  can aim a typed secret elsewhere. */
export const SECRET_ENDPOINT = "plugins/_ea_ansible/network_inventory";

export interface SecretForm {
  name: string;
  kind: string;
  username: string;
  note: string;
}

export interface Interaction {
  /**
   * Three weights on one transport.
   *
   * A `question` offers answers. An `approval` offers a decision and carries no
   * authority: clicking it sends a message, which the server cannot tell from
   * the user typing the same word, so it is only the agent checking before doing
   * something it may already do.
   *
   * A `decision` *is* the decision. It calls a named endpoint that checks the
   * permission server-side, because a decision with weight -- an engineer
   * approving the exact commands a change will push -- cannot ride on a chat
   * message: a message proves nothing about who sent it or what they were
   * looking at.
   */
  kind: "question" | "approval" | "decision" | "secret_form";
  id: string;
  /** `secret_form` only: the credential to collect. It carries no value. */
  secretForm?: SecretForm;
  question: string;
  /** Approvals and decisions: what happens if the user says yes. */
  consequence?: string;
  options: InteractionOption[];
  allowOther: boolean;
  multi: boolean;
  /**
   * Decisions only. The endpoint to call, and one payload per option in the same
   * order. Both come from the card; neither is constructed here, because a
   * client that built its own payload would be a second place the decision's
   * meaning is decided.
   */
  endpoint?: string;
  payloads?: unknown[];
  /** Decisions only, display only: the evidence a reader is deciding on. The
   *  approval payload (with its `expected_hash`) arrives in `payloads`, never
   *  built here. */
  details?: { label: string; value: string }[];
  commands?: string[];
  /** A free-code change's card: the whole code, shown collapsed. Display only. */
  code?: string;
  checks?: { label: string; ok: boolean; detail?: string; level?: "warn" }[];
  /** Epoch seconds. Past it the card goes quiet. */
  expiresAt?: number;
  tier?: string;
  /** A change's card: the diff lines, the commands that undo it (or the words that say there are
   *  none), and the text the person must type before Approve is enabled. All display; the typed
   *  text goes back as `confirmation` and the endpoint is what checks it. */
  diff?: string[];
  rollback?: string[];
  confirm?: string;
  /** Decisions only: the edit a free-form read's card offers. Its endpoint is fixed
   *  by `EDIT_ENDPOINTS`; a card naming any other offers no edit. */
  edit?: { endpoint: string; payload: Record<string, unknown> };
  /** An Azure change's card: the CR or SR number field. The pattern only lights Approve; the endpoint decides. */
  reference?: { label: string; pattern: string; example: string };
  /** Buttons that fetch the change document or runbook: each posts its payload to `DOCUMENT_ENDPOINT`. */
  documents?: { label: string; payload: Record<string, unknown> }[];
  /** An Azure change set's card: its steps in order, each collapsed over its plan lines. Display only. `adds` is
   *  the step's count of resources it makes, when the card gives one. */
  steps?: { n: number; title: string; lines: string[]; adds?: number }[];
}

const EDIT_ENDPOINTS = [
  "plugins/_ea_ansible/read_edit",
  "plugins/_ea_azure_change/azure_edit",
  "plugins/_ea_azure/azure_read_edit",
];
/** Fixed here as well as in the card (cards.py): a card naming any other endpoint offers no document button. */
export const DOCUMENT_ENDPOINT = "plugins/_ea_azure_change/azure_document";

function jsonList(raw: unknown): unknown[] {
  try {
    const parsed: unknown = JSON.parse(String(raw ?? "[]"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

const isRecord = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object";

const TONES: InteractionTone[] = ["go", "stop", "neutral"];

/**
 * The only endpoints a decision card may call: the three approvals, as
 * `AUTHORISED_ENDPOINTS` in plugins/_ea_interaction/helpers/cards.py has them.
 * That list is checked where the server builds a card. A card is drawn here
 * from a step's values, and values can come from elsewhere (see
 * `interactionOfItem`), so the list has to hold here as well: a button posts
 * its payload with the viewer's session to whatever it names.
 */
export const DECISION_ENDPOINTS: readonly string[] = [
  "plugins/_ea_ansible/change_approve",
  "plugins/_ea_azure/azure_read_approve",
  "plugins/_ea_azure_change/azure_approve",
];

/** The step type the server writes every card as (`log.log(type="info", ...)` in the card helpers). */
const CARD_STEP_TYPE = "info";

/**
 * The card a step is, if it is one. Only a step the server wrote as a card can
 * be: a tool call is logged with its arguments as its values (types `tool`,
 * `code_exe`, `subagent`, `mcp`, `progress`), and the arguments are the
 * model's to write. Read from the values alone, a tool call that said
 * `interaction: decision` was drawn as a working button.
 */
export function interactionOfItem(item: Pick<RawEntry, "type" | "kvps">): Interaction | null {
  return item.type === CARD_STEP_TYPE ? interactionOf(item.kvps) : null;
}

export function interactionOf(kvps: Record<string, unknown> | null | undefined): Interaction | null {
  const kind = kvps ? String(kvps.interaction ?? "") : "";
  if (kind !== "question" && kind !== "approval" && kind !== "decision" && kind !== "secret_form") return null;
  if (!kvps) return null;
  const question = String(kvps.question ?? "").trim();
  if (!question) return null;

  if (kind === "secret_form") {
    const name = String(kvps.credential_name ?? "").trim();
    // The executor's name rule; anything else would be refused on submit anyway.
    if (!name || !/^[A-Za-z0-9_-]+$/.test(name) || String(kvps.endpoint ?? "") !== SECRET_ENDPOINT) return null;
    return {
      kind,
      id: String(kvps.interaction_id ?? ""),
      question,
      options: [],
      allowOther: false,
      multi: false,
      secretForm: {
        name,
        kind: String(kvps.credential_kind ?? "") || "password",
        username: String(kvps.username ?? ""),
        note: String(kvps.note ?? ""),
      },
    };
  }

  let options: InteractionOption[] = [];
  try {
    const parsed: unknown = JSON.parse(String(kvps.options ?? "[]"));
    if (!Array.isArray(parsed)) return null;
    options = parsed
      .filter((o): o is { label: unknown; detail?: unknown } => Boolean(o) && typeof o === "object")
      .map((o) => {
        // Built conditionally rather than with `detail: undefined`: the project
        // has exactOptionalPropertyTypes, so an optional key must be absent.
        const label = String((o as { label: unknown }).label ?? "").trim();
        const raw = (o as { detail?: unknown }).detail;
        const detail = raw ? String(raw).trim() : "";
        const rawTone = String((o as { tone?: unknown }).tone ?? "") as InteractionTone;
        const tone = TONES.includes(rawTone) ? rawTone : undefined;
        return {
          label,
          ...(detail ? { detail } : {}),
          ...(tone ? { tone } : {}),
        };
      })
      .filter((o) => o.label.length > 0);
  } catch {
    // A card we cannot parse is not a card. Falling back to rendering it as an
    // ordinary step is right: the question text is still in the heading, so the
    // user can still answer it by typing.
    return null;
  }
  if (options.length === 0) return null;

  const consequence = String(kvps.consequence ?? "").trim();

  // The two kinds of approval are kept apart here, in both directions, because
  // on screen they are identical and getting it wrong is invisible.
  //
  // A decision without a usable endpoint and one payload per option is not a
  // decision. Degrading it to a plain approval would turn a button that records
  // an approval into one that sends a chat message saying "Approve".
  //
  // And an *approval* carrying an endpoint is refused outright rather than
  // having the endpoint ignored. That card is either a typo for a decision or
  // something trying to get authority onto the message path; either way,
  // rendering a working button that quietly authorises nothing is the worst of
  // the three options. Refusing shows it as an ordinary step, so the question is
  // still readable and still answerable by typing.
  let endpoint = "";
  let payloads: unknown[] | null = null;
  if (kind === "decision") {
    endpoint = String(kvps.endpoint ?? "").trim();
    try {
      const parsed: unknown = JSON.parse(String(kvps.payloads ?? "null"));
      payloads = Array.isArray(parsed) ? parsed : null;
    } catch {
      payloads = null;
    }
    if (!DECISION_ENDPOINTS.includes(endpoint) || !payloads || payloads.length !== options.length) return null;
  } else if (kvps.endpoint || kvps.payloads) {
    return null;
  }

  // Evidence a decision shows; a malformed list is dropped, never half-shown.
  const details = jsonList(kvps.details)
    .filter(isRecord)
    .map((r) => ({ label: String(r.label ?? ""), value: String(r.value ?? "") }))
    .filter((r) => r.label);
  const commands = jsonList(kvps.commands).map(String).filter(Boolean);
  const checks = jsonList(kvps.checks)
    .filter(isRecord)
    .map((r) => ({
      label: String(r.label ?? ""),
      ok: r.ok === true,
      ...(r.detail ? { detail: String(r.detail) } : {}),
      ...(r.level === "warn" ? { level: "warn" as const } : {}),
    }))
    .filter((r) => r.label);
  const code = typeof kvps.code === "string" ? kvps.code : "";
  const diff = jsonList(kvps.diff).map(String);
  const rollback = jsonList(kvps.rollback).map(String).filter(Boolean);
  const confirm = String(kvps.confirm ?? "").trim();
  const expiresAt = Number(kvps.expires_at);
  const tier = String(kvps.tier ?? "").trim();
  let edit: Interaction["edit"];
  if (kind === "decision" && String(kvps.editable) === "true" && EDIT_ENDPOINTS.includes(String(kvps.edit_endpoint))) {
    try {
      const payload: unknown = JSON.parse(String(kvps.edit_payload ?? "null"));
      if (isRecord(payload)) edit = { endpoint: String(kvps.edit_endpoint), payload };
    } catch {
      edit = undefined;
    }
  }

  // A suggestion is a question card that shows its evidence; an approval card shows none.
  const shows = kind === "decision" || kind === "question";
  let reference: Interaction["reference"];
  if (kind === "decision") {
    try {
      const parsed: unknown = JSON.parse(String(kvps.reference ?? "null"));
      if (isRecord(parsed) && parsed.pattern) {
        reference = {
          label: String(parsed.label ?? "") || "CR or SR number",
          pattern: String(parsed.pattern),
          example: String(parsed.example ?? ""),
        };
      }
    } catch {
      reference = undefined;
    }
  }
  // A set's steps are all shown or the card is not one: a card that listed some of what it approves would mislead.
  const stepRows = kind === "decision" ? jsonList(kvps.steps) : [];
  const steps = stepRows.filter(isRecord).map((r) => ({
    n: Number(r.n),
    title: String(r.title ?? ""),
    lines: Array.isArray(r.lines) ? r.lines.map(String) : [],
    ...(typeof r.adds === "number" ? { adds: r.adds } : {}),
  }));
  if (kind === "decision" && kvps.steps !== undefined && !stepRows.length) return null;
  if (steps.length !== stepRows.length || steps.some((r, i) => r.n !== i + 1 || !r.title)) return null;
  const documents =
    String(kvps.document_endpoint ?? "") === DOCUMENT_ENDPOINT
      ? jsonList(kvps.documents)
          .filter(isRecord)
          .filter((r) => isRecord(r.payload) && r.label)
          .map((r) => ({ label: String(r.label), payload: r.payload as Record<string, unknown> }))
      : [];

  return {
    kind,
    id: String(kvps.interaction_id ?? ""),
    question,
    ...(consequence ? { consequence } : {}),
    options,
    // An approval is a decision, never a multi-select: "approve and reject"
    // is not an answer.
    allowOther: String(kvps.allow_other ?? "true") !== "false",
    multi: kind === "question" && String(kvps.multi ?? "false") === "true",
    ...(kind === "decision" ? { endpoint, payloads: payloads ?? [] } : {}),
    ...(shows && details.length ? { details } : {}),
    ...(shows && commands.length ? { commands } : {}),
    ...(kind === "decision" && code ? { code } : {}),
    ...(shows && checks.length ? { checks } : {}),
    ...(kind === "decision" && Number.isFinite(expiresAt) && expiresAt > 0 ? { expiresAt } : {}),
    ...(shows && tier ? { tier } : {}),
    ...(kind === "decision" && diff.length ? { diff } : {}),
    ...(kind === "decision" && rollback.length ? { rollback } : {}),
    ...(kind === "decision" && confirm ? { confirm } : {}),
    ...(edit ? { edit } : {}),
    ...(reference ? { reference } : {}),
    ...(shows && documents.length ? { documents } : {}),
    ...(steps.length ? { steps } : {}),
  };
}

export function isInteraction(entry: Entry): boolean {
  return interactionOfItem(entry.item) !== null;
}

export interface Group {
  /** Steps run together, then an answer closes them. */
  kind: "steps";
  entries: Entry[];
  /** The most recent step's title -- what the group is currently doing. */
  title: string;
  seconds: number | null;
  /** How many of `entries` are housekeeping, hidden unless asked for. */
  quiet: number;
  /** How many of `entries` failed, so a folded group still says so. */
  failed: number;
}

export type Section = Group | { kind: "single"; entry: Entry };

function isAnswer(item: RawEntry): boolean {
  // A subordinate's response is part of the working, not the answer: only the
  // root agent is talking to the person.
  return (item.type || "").toLowerCase() === "response" && !(item.agentno && item.agentno > 0);
}

/**
 * Drop the root agent's own name from a heading.
 *
 * The backend prefixes every heading with the agent that emitted it -- `E0` for
 * the one the user is talking to, `E1` upward for subordinates it delegates to.
 * On the root that prefix is on every single line, which makes it noise: it
 * distinguishes nothing, and it pushes the part that does distinguish something
 * past the width of the column.
 *
 * Subordinate prefixes stay. There the number is the only thing saying which
 * agent did the work, and a transcript that hides that is lying about what ran.
 */
export function stripRootAgentPrefix(text: string, agentno?: number): string {
  if (agentno !== undefined && agentno !== 0) return text;
  return String(text ?? "").replace(/^E0:\s*/, "");
}

export function classify(item: RawEntry): Entry {
  const type = (item.type || "").toLowerCase();
  if (type === "user") {
    return { kind: "user", item, title: stripIconMarkers(item.heading || item.content || "") };
  }
  if (isAnswer(item)) {
    return { kind: "answer", item, text: String(item.content ?? "") };
  }
  let heading = stripRootAgentPrefix(stripIconMarkers(item.heading || ""), item.agentno);
  // "Calling LLM..." is the placeholder a step starts with; when the model
  // answered without a headline it is never replaced. Say what the step did.
  if (/Calling LLM/i.test(heading)) {
    const kvps = item.kvps ?? {};
    const headline = typeof kvps.headline === "string" ? kvps.headline.trim() : "";
    const tool = typeof kvps.tool_name === "string" ? kvps.tool_name.trim() : "";
    heading = heading.replace(/Calling LLM\.*/i, headline || (tool ? `Using ${tool.replace(/_/g, " ")}` : "Thinking…"));
  }
  const fallback = stripRootAgentPrefix(
    stripIconMarkers(String(item.content ?? "")).split("\n")[0] ?? "",
    item.agentno,
  );
  return {
    kind: "step",
    item,
    code: stepCode(type),
    title: heading || fallback || type,
  };
}

/**
 * What to call a group, which is the last thing it said about itself.
 *
 * The agent's own narration, not the last step: a run usually ends on tool
 * output, and "[0] File created" describes the command rather than the work.
 * Falls back to the last step when nothing narrated.
 */
function groupTitle(entries: Entry[]): string {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const entry = entries[i];
    // Placeholders ("Thinking…", "Receiving...") say a step was running, not what the work was.
    if (entry?.kind === "step" && entry.code === "GEN" && entry.title && !/^(Thinking|Receiving)/.test(entry.title)) {
      return entry.title;
    }
  }
  // Never title a group with a line the reader cannot see.
  const visible = entries.filter((e) => !isQuiet(e));
  const last = visible[visible.length - 1] ?? entries[entries.length - 1];
  return last?.kind === "step" && last.title ? last.title : "Working";
}

function elapsed(entries: Entry[]): number | null {
  const times = entries
    .map((e) => (e.item.timestamp ? Date.parse(e.item.timestamp) : NaN))
    .filter((t) => Number.isFinite(t));
  if (times.length < 2) return null;
  return Math.max(0, Math.round((Math.max(...times) - Math.min(...times)) / 1000));
}

/** Group consecutive steps; answers and user messages stand alone. */
export function buildSections(items: RawEntry[]): Section[] {
  const sections: Section[] = [];
  let run: Entry[] = [];

  const closeRun = () => {
    if (!run.length) return;
    sections.push({
      kind: "steps",
      entries: run,
      title: groupTitle(run),
      seconds: elapsed(run),
      quiet: run.filter(isQuiet).length,
      failed: run.filter(isFailure).length,
    });
    run = [];
  };

  for (const item of items) {
    const entry = classify(item);
    if (entry.kind === "step" && !hasAttachment(entry) && !isInteraction(entry)) {
      run.push(entry);
      continue;
    }
    closeRun();
    sections.push({ kind: "single", entry });
  }
  closeRun();
  return sections;
}

/**
 * Whether a group should start folded.
 *
 * Folded once its answer has arrived, open while it is still the live one, so
 * progress is visible as it happens and tidy afterwards.
 */
export function isGroupClosed(sections: Section[], index: number): boolean {
  return sections.slice(index + 1).some((s) => s.kind === "single" && s.entry.kind === "answer");
}

/**
 * Whether a question is still the one being asked.
 *
 * Answered the same way `isGroupClosed` decides a group is finished: by what
 * came after it, not by a timestamp or by server state. Anything the user said,
 * or any answer the agent gave, means the conversation moved on.
 *
 * Without this, scrolling up and clicking a question from twenty messages ago
 * sends an answer to something the agent has long since stopped asking -- which
 * is worse than no card at all, because it looks like it worked.
 *
 * A decision (an approval that calls an endpoint) is different: it can wait
 * days for a change number while the conversation goes on, and the agent always
 * says something after drawing it. Its endpoint is the authority (it refuses a
 * decided, expired or re-planned change), so the card stays live until a newer
 * card for the same change replaces it. Seen on the first real Azure change: the
 * agent's "the card is ready" message switched the card off.
 */
export function isInteractionLive(sections: Section[], index: number): boolean {
  const later = sections.slice(index + 1);
  const current = sections[index];
  const change = current ? decisionChange(current) : "";
  if (change) return !later.some((s) => decisionChange(s) === change);
  return !later.some((s) => s.kind === "single" && (s.entry.kind === "answer" || s.entry.kind === "user"));
}

/** The change a decision card decides, from its first payload; "" for anything else. */
function decisionChange(section: Section): string {
  if (section.kind !== "single") return "";
  const kvps = section.entry.item.kvps;
  if (!kvps || String(kvps.interaction ?? "") !== "decision") return "";
  try {
    const payloads: unknown = JSON.parse(String(kvps.payloads ?? "[]"));
    const first: unknown = Array.isArray(payloads) ? payloads[0] : null;
    return first && typeof first === "object" ? String((first as { change_id?: unknown }).change_id ?? "") : "";
  } catch {
    return "";
  }
}

/**
 * Finding something in a conversation that is already on screen.
 *
 * Browser find already works on what is rendered, so duplicating it would be
 * work for nothing. What it cannot see is the part this grammar hides: steps
 * folded into a closed group, and answers clamped at ANSWER_CLAMP_CHARS. Those
 * are exactly the places a half-remembered detail ends up.
 *
 * So this searches the data rather than the DOM, and reports where each match
 * is, so the interface can open what needs opening.
 */
export interface Match {
  /** Index into the sections array — which group or single holds it. */
  section: number;
  /** The log entry's own number, stable across re-grouping. */
  no: number;
  /** True when it sits inside a step group, i.e. possibly folded away. */
  folded: boolean;
}

function entryText(entry: Entry): string {
  const item = entry.item;
  return `${item.heading ?? ""}\n${item.content ?? ""}`;
}

export function findMatches(sections: Section[], term: string): Match[] {
  const needle = term.trim().toLowerCase();
  if (needle.length < 2) return [];

  const matches: Match[] = [];
  sections.forEach((section, index) => {
    if (section.kind === "steps") {
      for (const entry of section.entries) {
        if (entryText(entry).toLowerCase().includes(needle)) {
          matches.push({ section: index, no: entry.item.no, folded: true });
        }
      }
      return;
    }
    if (entryText(section.entry).toLowerCase().includes(needle)) {
      matches.push({ section: index, no: section.entry.item.no, folded: false });
    }
  });
  return matches;
}

/** Which sections hold a match, so a closed group can be opened for it. */
export function sectionsWithMatches(matches: Match[]): Set<number> {
  return new Set(matches.map((match) => match.section));
}
