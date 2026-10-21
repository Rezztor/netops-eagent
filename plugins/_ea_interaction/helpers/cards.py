"""The shapes an interaction card may take, defined once.

A card is an ordinary log item whose `kvps` the interface recognises. That is not
a new transport: `plugins/_ea_attachments` already does it, and its own comment
says why it was cheap -- the kvps already travel to the client, so a control in
the transcript needs no protocol change.

Everything here is validation. The agent supplies a question and some options;
this decides whether that is a card at all, and refuses rather than rendering
something the user cannot act on.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

KIND_QUESTION = "question"
KIND_APPROVAL = "approval"
KIND_DECISION = "decision"
KIND_SECRET_FORM = "secret_form"

#: Where a secret form submits, fixed here and never taken from the agent. The
#: frontend refuses a card naming anything else, so the agent can neither aim the
#: secret elsewhere nor read it: the card has no field the model fills but the
#: credential's *name*.
SECRET_ENDPOINT = "plugins/_ea_ansible/network_inventory"

#: The executor's own credential kinds (executor/credentials.py KINDS); the
#: executor is a separate image, so the list is repeated, not imported.
SECRET_KINDS = ("password", "ssh_key", "api_token", "enable")

#: The only endpoints a decision card may call, by name.
#:
#: This list is the reason a decision card is safe to exist. The card is built by
#: the agent, and an endpoint field the agent could fill freely would be a button
#: that calls anything the *viewer* is allowed to call -- the agent choosing the
#: target and the user unknowingly supplying the authority. An allowlist means
#: the worst an agent can do is render a button for a decision a person was
#: already going to be asked to make.
#:
#: Adding a name here is a security decision. The question to answer first is
#: what happens if the agent renders that button at a moment of its own choosing,
#: with a prompt of its own wording.
AUTHORISED_ENDPOINTS: frozenset[str] = frozenset(
    {
        "plugins/_ea_ansible/change_approve",
        "plugins/_ea_azure/azure_read_approve",
        "plugins/_ea_azure_change/azure_approve",
    }
)

#: The only endpoint a decision card may offer for editing what it asks about. It
#: is display-plus-call like the decision endpoints: the card carries the payload,
#: the endpoint checks who is asking and that the card is still open.
EDIT_ENDPOINTS: frozenset[str] = frozenset(
    {"plugins/_ea_ansible/read_edit", "plugins/_ea_azure_change/azure_edit", "plugins/_ea_azure/azure_read_edit"}
)

#: Where a document button posts its payload (the file comes back as a download). Fixed in code like the others, so the
#: agent supplies only the label and the payload, never where it goes; the frontend re-checks the name.
DOCUMENT_ENDPOINT = "plugins/_ea_azure_change/azure_document"
DOCUMENT_ENDPOINTS: frozenset[str] = frozenset({DOCUMENT_ENDPOINT})
DOCUMENTS_MAX = 4

#: How a decision reads, so the interface does not have to guess it from the
#: label. Matching on the word "approve" would work until someone writes
#: "Go ahead", which is exactly when it matters.
TONES = ("go", "stop", "neutral")

#: Two is the fewest that is a question rather than a statement. Five is a menu
#: people read instead of answering, and the reading costs more than typing
#: would have.
MIN_OPTIONS = 2
MAX_OPTIONS = 4

LABEL_MAX = 60
DETAIL_MAX = 120
QUESTION_MAX = 300

#: What a decision card may show beside the question. Capped per list because
#: each travels as one kvps value under VALUE_BUDGET.
TIERS = ("read", "change-low", "change-high", "suggestion")
DIFF_MAX = 60
DETAIL_ROWS_MAX = 20
COMMANDS_MAX = 40
CHECKS_MAX = 20
LINE_MAX = 300
#: The CR or SR number's pattern and example, display only: the endpoint decides what matches.
REFERENCE_PATTERN_MAX = 100
REFERENCE_EXAMPLE_MAX = 40

#: A change set's card (an Azure change of ordered steps): the most steps, and the longest title. The most is the executor's
#: `SET_MAX_STEPS`, repeated because this plugin does not import the executor (a test holds the two equal). A title is
#: never cut: what the person approves is what they read.
STEPS_MAX = 12
STEP_TITLE_MAX = 120
STEP_MORE = "... {} more lines in the change document"

#: `helpers/log.py` truncates any kvps value past this, and a silently truncated
#: options list renders as broken JSON rather than as an error.
VALUE_BUDGET = 5000


class CardRejected(Exception):
    """The card would not have been usable, so it was not emitted."""


def _encoded(rows: Any, limit: int, name: str) -> str:
    text = json.dumps(rows, ensure_ascii=False)
    if len(text) > VALUE_BUDGET:
        raise CardRejected(f"The card's '{name}' are too long to travel as a card.")
    return text


def _steps(steps: Any) -> str:
    """The steps of a change set as one value: `[{n, title, lines, adds?}]`, in order.

    A title that does not fit is refused. The plan lines are the one thing cut, and only here: they are dealt out one
    at a time across the steps so no step is left bare while another keeps all of its lines, and each step that lost
    some says how many.
    """
    if not isinstance(steps, (list, tuple)) or not 1 <= len(steps) <= STEPS_MAX:
        raise CardRejected(f"'steps' must be a list of 1 to {STEPS_MAX} steps.")
    rows: list[dict[str, Any]] = []
    wanted: list[list[str]] = []
    for position, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or step.get("n") != position or isinstance(step.get("n"), bool):
            raise CardRejected("Each step must be an object numbered in order from 1.")
        row: dict[str, Any] = {"n": position, "title": _clean(step.get("title"), STEP_TITLE_MAX, "step title"), "lines": []}
        adds = step.get("adds")
        if adds is not None:
            if isinstance(adds, bool) or not isinstance(adds, int) or adds < 0:
                raise CardRejected("A step's 'adds' must be a count.")
            row["adds"] = adds
        rows.append(row)
        wanted.append([str(l)[:LINE_MAX] for l in step.get("lines") or [] if str(l).strip()])
    room = VALUE_BUDGET - 60 * len(rows)  # what the "... N more lines" notes will need
    if len(json.dumps(rows, ensure_ascii=False)) > room:
        raise CardRejected("The steps' titles are too long to travel as a card.")
    full: set[int] = set()
    for depth in range(max(map(len, wanted), default=0)):
        for i, lines in enumerate(wanted):
            if i in full or depth >= len(lines):
                continue
            rows[i]["lines"].append(lines[depth])
            if len(json.dumps(rows, ensure_ascii=False)) > room:
                rows[i]["lines"].pop()
                full.add(i)
    for row, lines in zip(rows, wanted):
        if len(row["lines"]) < len(lines):
            row["lines"].append(STEP_MORE.format(len(lines) - len(row["lines"])))
    return _encoded(rows, VALUE_BUDGET, "steps")


def _extras(
    details, commands, checks, expires_at, tier, diff=None, rollback=None, confirm="", reference=None, documents=None, code=None,
    steps=None,
) -> dict[str, str]:
    """The optional evidence a decision card shows: what, where, what would run."""
    out: dict[str, str] = {}
    if details:
        if len(details) > DETAIL_ROWS_MAX:
            raise CardRejected(f"'details' has more than {DETAIL_ROWS_MAX} rows.")
        rows = []
        for row in details:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise CardRejected("Each detail must be a (label, value) pair.")
            rows.append(
                {"label": _clean(row[0], LABEL_MAX, "detail label"), "value": str(row[1] or "").strip()[:LINE_MAX]}
            )
        out["details"] = _encoded(rows, VALUE_BUDGET, "details")
    if commands:
        if len(commands) > COMMANDS_MAX:
            raise CardRejected(f"'commands' has more than {COMMANDS_MAX} lines.")
        out["commands"] = _encoded([_clean(c, LINE_MAX, "command") for c in commands], VALUE_BUDGET, "commands")
    if code:
        # Free code, shown whole and collapsed. One value is cut at VALUE_BUDGET, so over it the card is refused, never
        # shown cut: what is approved is what is read.
        if not isinstance(code, str) or len(code) > VALUE_BUDGET:
            raise CardRejected(f"The code is longer than {VALUE_BUDGET} characters, which is more than a card can show whole.")
        out["code"] = code
    if steps:
        out["steps"] = _steps(steps)
    if checks:
        if len(checks) > CHECKS_MAX:
            raise CardRejected(f"'checks' has more than {CHECKS_MAX} entries.")
        rows = []
        for check in checks:
            if not isinstance(check, dict):
                raise CardRejected("Each check must be an object with a label and ok.")
            row = {"label": _clean(check.get("label"), LABEL_MAX, "check label"), "ok": bool(check.get("ok"))}
            level = str(check.get("level") or "").strip()
            if level:
                # Amber and never blocking; a failed check still means no card, so only advice uses it.
                if level != "warn":
                    raise CardRejected(f"'{level}' is not a check level; use 'warn' or leave it out.")
                row["level"] = level
            detail = str(check.get("detail") or "").strip()
            if detail:
                row["detail"] = detail[:DETAIL_MAX]
            rows.append(row)
        out["checks"] = _encoded(rows, VALUE_BUDGET, "checks")
    if diff:
        # A diff is context for the commands, not the thing approved: cut it to fit
        # rather than refuse the card.
        shown = [str(line)[:LINE_MAX] for line in diff[:DIFF_MAX]]
        while shown and len(json.dumps(shown, ensure_ascii=False)) > VALUE_BUDGET - 60:
            shown.pop()
        if len(shown) < len(diff):
            shown.append(f"... {len(diff) - len(shown)} more lines")
        out["diff"] = json.dumps(shown, ensure_ascii=False)
    if rollback:
        # The commands that undo it, or the words that say there are none.
        out["rollback"] = _encoded([_clean(c, LINE_MAX, "rollback") for c in rollback], VALUE_BUDGET, "rollback")
    if confirm:
        # The text the person must type before Approve lights up; the typed text
        # comes back as `confirmation` and the endpoint, not the card, checks it.
        out["confirm"] = _clean(confirm, LABEL_MAX, "confirm")
    if reference:
        if not isinstance(reference, dict):
            raise CardRejected("'reference' must be an object with a label, a pattern and an example.")
        pattern = _clean(reference.get("pattern"), REFERENCE_PATTERN_MAX, "reference pattern")
        try:
            re.compile(pattern)
        except re.error:
            raise CardRejected("The reference pattern is not a valid pattern.") from None
        out["reference"] = json.dumps(
            {
                "label": _clean(reference.get("label"), LABEL_MAX, "reference label"),
                "pattern": pattern,
                "example": str(reference.get("example") or "").strip()[:REFERENCE_EXAMPLE_MAX],
            },
            ensure_ascii=False,
        )
    if documents:
        if len(documents) > DOCUMENTS_MAX:
            raise CardRejected(f"'documents' has more than {DOCUMENTS_MAX} entries.")
        rows = []
        for doc in documents:
            if not isinstance(doc, dict) or not isinstance(doc.get("payload"), dict):
                raise CardRejected("Each document must be an object with a label and a payload.")
            rows.append({"label": _clean(doc.get("label"), LABEL_MAX, "document label"), "payload": doc["payload"]})
        out["documents"] = _encoded(rows, VALUE_BUDGET, "documents")
        out["document_endpoint"] = DOCUMENT_ENDPOINT
    if expires_at is not None:
        try:
            out["expires_at"] = repr(float(expires_at))
        except (TypeError, ValueError):
            raise CardRejected("'expires_at' must be a timestamp.") from None
    if tier:
        if tier not in TIERS:
            raise CardRejected(f"'{tier}' is not a tier; use one of {TIERS}.")
        out["tier"] = tier
    return out


def _clean(value: Any, limit: int, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CardRejected(f"'{field}' cannot be empty.")
    if len(text) > limit:
        raise CardRejected(f"'{field}' is longer than {limit} characters.")
    return text


def normalise_options(raw: Any) -> list[dict[str, str]]:
    """Accept a list of strings or of {label, detail} objects.

    Strings are the common case and the model should not have to write objects
    to ask a simple question.
    """
    if not isinstance(raw, (list, tuple)):
        raise CardRejected("'options' must be a list.")

    options: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            options.append({"label": _clean(item, LABEL_MAX, "option")})
            continue
        if not isinstance(item, dict):
            raise CardRejected("Each option must be a string or an object with a label.")
        option = {"label": _clean(item.get("label"), LABEL_MAX, "option label")}
        tone = str(item.get("tone") or "").strip().lower()
        if tone:
            if tone not in TONES:
                raise CardRejected(f"'{tone}' is not a decision tone; use one of {TONES}.")
            option["tone"] = tone
        detail = str(item.get("detail") or "").strip()
        if detail:
            if len(detail) > DETAIL_MAX:
                raise CardRejected(f"An option detail is longer than {DETAIL_MAX} characters.")
            option["detail"] = detail
        options.append(option)

    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise CardRejected(
            f"A question needs between {MIN_OPTIONS} and {MAX_OPTIONS} options; "
            f"this one has {len(options)}. Fewer is a statement, more is a menu."
        )

    labels = [option["label"].casefold() for option in options]
    if len(set(labels)) != len(labels):
        raise CardRejected("Two options have the same label, so the answer would be ambiguous.")

    return options


def question_card(
    question: Any,
    options: Any,
    *,
    allow_other: bool = True,
    multi: bool = False,
    interaction_id: str | None = None,
    details: list[tuple[str, str]] | None = None,
    checks: list[dict] | None = None,
    commands: list[str] | None = None,
    tier: str | None = None,
    documents: list[dict] | None = None,
) -> dict[str, str]:
    """Build the kvps for a question card, or raise CardRejected.

    `details`, `checks`, `commands`, `tier` and `documents` are display only, as on a decision card. A suggestion (tier
    `suggestion`) is a question card: its options send a chat message and authorise nothing, and it has no endpoint.

    Values are strings because that is what travels: the option list goes as one
    JSON value rather than as numbered keys, so the interface parses one thing
    and a malformed card is obvious instead of partial.
    """
    text = _clean(question, QUESTION_MAX, "question")
    parsed = normalise_options(options)
    encoded = json.dumps(parsed, ensure_ascii=False)
    if len(encoded) > VALUE_BUDGET:
        raise CardRejected(
            "These options are too long to travel as a card. Shorten the details, "
            "or ask the question in prose."
        )

    card = {
        "interaction": KIND_QUESTION,
        "interaction_id": interaction_id or f"q_{uuid.uuid4().hex[:10]}",
        "question": text,
        "options": encoded,
        "allow_other": "true" if allow_other else "false",
        "multi": "true" if multi else "false",
    }
    card.update(_extras(details, commands, checks, None, tier, documents=documents))
    return card


def approval_card(
    prompt: Any,
    *,
    consequence: str = "",
    approve_label: str = "Approve",
    reject_label: str = "Reject",
    allow_other: bool = True,
    interaction_id: str | None = None,
) -> dict[str, str]:
    """Build the kvps for a conversational approval.

    **This card cannot authorise anything.** Clicking it sends an ordinary
    message, and the server cannot tell that from the user typing the same word
    -- so it is only ever the agent pausing to check before doing something it is
    already permitted to do. There is deliberately no field here for an endpoint:
    an approval that carries authority is a different card, built with its own
    tests, and leaving a half-wired one lying about is how the distinction gets
    lost.
    """
    text = _clean(prompt, QUESTION_MAX, "prompt")
    options = normalise_options(
        [
            {"label": _clean(approve_label, LABEL_MAX, "approve label"), "tone": "go"},
            {"label": _clean(reject_label, LABEL_MAX, "reject label"), "tone": "stop"},
        ]
    )

    card = {
        "interaction": KIND_APPROVAL,
        "interaction_id": interaction_id or f"a_{uuid.uuid4().hex[:10]}",
        "question": text,
        "options": json.dumps(options, ensure_ascii=False),
        "allow_other": "true" if allow_other else "false",
        "multi": "false",
    }
    consequence = str(consequence or "").strip()
    if consequence:
        if len(consequence) > QUESTION_MAX:
            raise CardRejected(f"'consequence' is longer than {QUESTION_MAX} characters.")
        card["consequence"] = consequence
    return card


def decision_card(
    prompt: Any,
    *,
    endpoint: str,
    approve_payload: dict[str, Any],
    reject_payload: dict[str, Any],
    consequence: str = "",
    approve_label: str = "Approve",
    reject_label: str = "Reject",
    interaction_id: str | None = None,
    details: list[tuple[str, str]] | None = None,
    commands: list[str] | None = None,
    checks: list[dict] | None = None,
    expires_at: float | None = None,
    tier: str | None = None,
    edit_endpoint: str | None = None,
    edit_payload: dict[str, Any] | None = None,
    diff: list[str] | None = None,
    rollback: list[str] | None = None,
    confirm: str = "",
    reference: dict[str, str] | None = None,
    documents: list[dict] | None = None,
    code: str | None = None,
    steps: list[dict] | None = None,
) -> dict[str, str]:
    """Build a card that *is* the decision, rather than one that informs it.

    The distinction this and `approval_card` sit either side of is the whole
    reason there are two functions.

    An **approval card** is the agent pausing to check before doing something it
    is already permitted to do. Clicking it sends a message, and the server
    cannot tell that from the user typing the same word -- so it carries no
    authority and must not pretend to.

    A **decision card** records a decision that has legal weight in the system:
    an engineer approving the exact commands a change will push. That cannot ride
    on a chat message, because a message proves nothing about who sent it or what
    they were looking at. So it calls a named endpoint, the endpoint checks
    `network.approve` server-side, and the click is evidence only because the
    endpoint says it is.

    Neither the endpoint nor the payload is free-form. The endpoint must be in
    `AUTHORISED_ENDPOINTS`; the payload is carried as opaque JSON and is
    meaningful only to the endpoint that receives it.

    `details`, `commands`, `checks`, `diff`, `rollback`, `expires_at` and `tier` are display only:
    they show the reader what they are deciding on and carry no authority.
    `edit_endpoint` (from `EDIT_ENDPOINTS`) with its `edit_payload` makes the
    commands editable on the card; the edit changes nothing until it is approved.
    `confirm` keeps Approve disabled until the person types that text, which then
    travels in the approve payload; the endpoint refuses a mismatch (ADR 0050).
    `reference` ({label, pattern, example}) shows a field for the CR or SR number and keeps Approve disabled until it
    matches `pattern`; the typed value travels in the approve payload as `reference`, and the endpoint, not the pattern
    on the card, decides. `documents` ([{label, payload}]) are buttons that fetch the change document (ADR 0066).
    `code` (one string, at most `VALUE_BUDGET` characters) is free code, shown whole in a collapsed block.
    `steps` ([{n, title, lines}], at most `STEPS_MAX`) are the ordered steps of an Azure change set, each shown collapsed
    with its plan lines; display only, like `diff`.
    """
    text = _clean(prompt, QUESTION_MAX, "prompt")
    if tier == "suggestion":
        raise CardRejected("A suggestion authorises nothing, so it is a question card and never carries an endpoint.")

    endpoint = str(endpoint or "").strip().strip("/")
    if endpoint not in AUTHORISED_ENDPOINTS:
        raise CardRejected(
            f"'{endpoint}' is not an endpoint a decision card may call. A card that "
            "could name any endpoint would be a button the agent aims and the "
            "viewer fires."
        )

    options = normalise_options(
        [
            {"label": _clean(approve_label, LABEL_MAX, "approve label"), "tone": "go"},
            {"label": _clean(reject_label, LABEL_MAX, "reject label"), "tone": "stop"},
        ]
    )
    payloads = json.dumps(
        [approve_payload, reject_payload], ensure_ascii=False, default=str
    )
    if len(payloads) > VALUE_BUDGET:
        raise CardRejected("The decision's payload is too long to travel as a card.")

    card = {
        "interaction": KIND_DECISION,
        "interaction_id": interaction_id or f"d_{uuid.uuid4().hex[:10]}",
        "question": text,
        "options": json.dumps(options, ensure_ascii=False),
        "endpoint": endpoint,
        "payloads": payloads,
        # A decision is one of the two offered. "Something else" is a reply, not
        # a decision, and an endpoint has nothing to do with a sentence.
        "allow_other": "false",
        "multi": "false",
    }
    card.update(_extras(details, commands, checks, expires_at, tier, diff, rollback, confirm, reference, documents, code, steps))
    if edit_endpoint:
        if edit_endpoint not in EDIT_ENDPOINTS:
            raise CardRejected(f"'{edit_endpoint}' is not an endpoint a card may offer for editing.")
        card.update(editable="true", edit_endpoint=edit_endpoint, edit_payload=json.dumps(edit_payload or {}))
    consequence = str(consequence or "").strip()
    if consequence:
        if len(consequence) > QUESTION_MAX:
            raise CardRejected(f"'consequence' is longer than {QUESTION_MAX} characters.")
        card["consequence"] = consequence
    return card


def secret_form_card(
    name: Any,
    kind: str = "password",
    username: str = "",
    note: str = "",
    *,
    interaction_id: str | None = None,
) -> dict[str, str]:
    """Build the kvps for a form that collects a device secret in the browser.

    The secret never touches the chat: the user types it into a password input
    and the browser posts it straight to `SECRET_ENDPOINT`. Nothing here, and
    nothing the agent can supply, carries a secret -- only the name it will be
    stored under, which is the same string the inventory already shows the model.
    """
    name = _clean(name, LABEL_MAX, "name")
    # The executor's rule (credentials.py put), so a card is never built that the
    # executor would refuse on submit.
    if not name.replace("-", "").replace("_", "").isalnum():
        raise CardRejected("A credential name may contain letters, digits, hyphens and underscores.")
    kind = str(kind or "password").strip()
    if kind not in SECRET_KINDS:
        raise CardRejected(f"'{kind}' is not a credential kind; use one of {SECRET_KINDS}.")
    username = str(username or "").strip()
    if len(username) > LABEL_MAX:
        raise CardRejected(f"'username' is longer than {LABEL_MAX} characters.")
    note = str(note or "").strip()
    if len(note) > QUESTION_MAX:
        raise CardRejected(f"'note' is longer than {QUESTION_MAX} characters.")

    return {
        "interaction": KIND_SECRET_FORM,
        "interaction_id": interaction_id or f"s_{uuid.uuid4().hex[:10]}",
        "question": f"Enter the credential '{name}' securely",
        "endpoint": SECRET_ENDPOINT,
        "credential_name": name,
        "credential_kind": kind,
        "username": username,
        "note": note,
    }


def show_secret_form(agent: Any, name: Any, **fields: str) -> dict[str, str]:
    """Build the form and put it in front of the user. Raises CardRejected."""
    from helpers.parallel_tools import user_facing_context

    kvps = secret_form_card(name, **fields)
    user_facing_context(agent).log.log(
        type="info",
        heading=kvps["question"],
        content="The value is typed into the form, not into chat.",
        kvps=kvps,
    )
    return kvps
