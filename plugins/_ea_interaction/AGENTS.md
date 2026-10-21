# Interaction Cards Plugin DOX

## Purpose

- Own the agent's ability to ask the user a question with answers they can click.
- Keep that on the transport the interface already has, rather than inventing one.
- Keep a question cheap: no blocking, no held loop, no timeout.

## Ownership

- `helpers/cards.py` owns the `kvps` shapes and every rule about what is a usable
  card. It is the only place that decides a question is malformed.
- `tools/ask_user.py` and `tools/request_approval.py` own the two tool surfaces.
  Both end the turn; neither waits.
- `prompts/agent.system.tool.ask_user.md` owns telling the model when *not* to
  use a card.
- `frontend/src/lib/transcript.ts` owns detection (`interactionOf`,
  `isInteractionLive`); `frontend/src/routes/InteractionCard.tsx` owns rendering.
- A card is drawn only from a step of type `info`, which is what every card helper logs (`interactionOfItem`): a tool call
  is logged with its arguments as its values, and those are the model's to write. A decision may name only an endpoint in
  `DECISION_ENDPOINTS`, the same three as `cards.AUTHORISED_ENDPOINTS` (`tests/test_cards.py` keeps them one list). A card
  helper must log with `type="info"`, and nothing may log model-supplied values under that type.

## Local Contracts

- **A card is a log item.** Its `kvps` carry the question; the interface
  recognises the shape. `plugins/_ea_attachments` established this and no
  protocol change was needed for either.
- **Asking ends the turn.** `ask_user` returns `break_loop=True` like `response`.
  It must never wait for the answer: blocking means a timeout, a held agent loop
  and a model context kept open for as long as a person takes to decide.
- **The answer is an ordinary message.** Clicking sends what the user would have
  typed. That is why this needs no new endpoint, and it is also the reason a card
  may never *authorise* anything — a message cannot be told apart from someone
  typing it. An approval that carries authority calls a permission-checked
  endpoint instead; see the chat interaction plan.
- **Two to four options.** Fewer is a statement, more is a menu people read
  instead of answering.
- **An approval carries no authority, and there is no field through which it
  could.** `approval_card` has no endpoint parameter, and a test asserts the card
  it builds contains none. A card that authorises has to call a
  permission-checked endpoint, because a message cannot be told apart from
  someone typing one -- and if that ever changes here, approving a production
  change becomes something anyone with chat access can do, with nothing visibly
  breaking.
- **A decision's colour comes from its `tone`, never from its label.** Matching
  on the word "approve" works until someone writes "Go ahead", which is exactly
  when getting it wrong matters.
- **An approval is never multi-select.** "Approve and reject" is not an answer,
  so the interface forces it off regardless of what the card says.
- **A malformed card is refused, and the turn does not end.** Returning
  `break_loop=False` with an explanation lets the agent ask better on the next
  step rather than leaving the user looking at a finished turn with nothing on it.
- **A card goes quiet when the conversation moves past it.** Decided from what
  came after it, not from a timestamp or server state. An answer to a question
  the agent stopped asking looks like it worked, which is the worst failure
  available here.
- **A decision card can show what is being approved.** `details`, `commands`, `checks`, `expires_at` and `tier` (reads and
  changes), `editable` with its `edit_endpoint` (a free-form read), and for a change `diff`, `rollback` and `confirm`:
  when `confirm` is set the Approve button stays disabled until the person types that text, which is added to the
  payload the card was given as `confirmation`. The interface still never builds a payload of its own.
- **An Azure change's card asks for a CR or SR number and offers the change document (ADR 0066).** `reference`
  ({label, pattern, example}) shows a field that keeps Approve disabled until the whole value matches `pattern` (full
  match, ignoring case, 40 characters at most); the value travels in the approve payload as `reference`. The pattern is
  display only: the endpoint decides, and a pattern the browser cannot read leaves Approve off. `code` (one string, at
  most `VALUE_BUDGET` characters, over it `CardRejected`) is free code shown whole in a collapsed block under the commands. `documents`
  ([{label, payload}]) are buttons that post their payload to `cards.DOCUMENT_ENDPOINT` (fixed in code, re-checked by
  `transcript.ts`) and save the file. A check with `level: "warn"` is amber and never blocks. A **suggestion** is a
  question card with tier `suggestion`: it shows its runbook and sends a chat message, `decision_card` refuses that
  tier, and no card of that tier can carry an endpoint.
- **A change set's card lists its steps.** `steps` ([{n, title, lines}], at most `STEPS_MAX` = the executor's
  `SET_MAX_STEPS`, numbered from 1 in order, optional `adds`) is one JSON value; each step is a collapsed block over its
  plan lines (coloured as `diff` is) and, after the decision, a state chip from the `azure_changes` row's `steps`. A title
  is never cut (`CardRejected`); the lines are dealt out evenly across the steps and each step that lost some ends with
  "... N more lines in the change document". `transcript.ts` shows all the steps or treats the card as not a decision.
- **A `secret_form` card collects a device secret without it being chat.** Its
  endpoint is `cards.SECRET_ENDPOINT`, fixed in code and re-checked by the
  frontend (`SECRET_ENDPOINT` in `transcript.ts`); the agent supplies only the
  credential *name*. The browser posts the value straight to the endpoint, which
  keeps its `plugins.manage` gate (without it the card shows a note and no field);
  the chat only ever gets "Credential <name> stored.". No field, test or log may
  carry the value.
- **`kvps` values are strings.** The option list travels as one JSON value, so a
  card that would be truncated is refused rather than rendered broken.

## Work Guidance

- New card kinds go in `helpers/cards.py` beside `question_card`, with their own
  validation. The interface should be able to tell them apart by
  `kvps["interaction"]` alone.
- Resist making the model's life easier at the reader's expense: an option's
  `detail` carries the consequence, never a recommendation.

## Verification

- `pytest plugins/_ea_interaction -q`
- `cd frontend && npx vitest run src/lib/__tests__/transcript.test.ts`

## Child DOX Index

No child DOX files.
