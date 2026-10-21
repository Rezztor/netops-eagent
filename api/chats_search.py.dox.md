# chats_search.py DOX

## Purpose

- Own the `chats_search.py` API endpoint.
- This module searches the signed-in user's own conversations for a substring, over what was actually said rather than over the whole serialized chat file.
- Keep this file-level DOX profile synchronized with `chats_search.py` because this directory is intentionally flat.

## Ownership

- `chats_search.py` owns the runtime implementation.
- `chats_search.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `_search_transcript(document: Any, needle: str) -> tuple[int, str]`: count matches among `user`/`response` log entries and quote the first hit.
- `_quote(content: str, at: int, length: int) -> str`: build a bounded snippet around one match, with ellipses where truncated.
- Classes:
- `ChatsSearch` (`ApiHandler`)
  - `async process(self, input: Input, request: Request) -> Output`
- Constants: `SEARCHED_TYPES` (`"user"`, `"response"`), `MAX_RESULTS` (50), `DEFAULT_RESULTS` (20), `SNIPPET_RADIUS` (60), `MIN_QUERY` (2).

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `ChatsSearch` uses the `ApiHandler` defaults: `requires_auth()` True, `requires_csrf()` True, method `POST`, no `required_permission()` beyond being signed in.
- Scoping is structural, not filtered after the fact: the query does not mention `owner_id` at all — `helpers.db.transaction()` sets the row-level-security scope to the caller, and the PostgreSQL policy on `conversations` is what actually keeps one user's chats out of another's results.
- Only searches `log.logs` entries whose `type` is `user` or `response` — never the whole serialized `chat.json`, which is mostly the rendered system prompt (`ctx_window.text`: rules, tool definitions, the system manual). A substring search over the raw file would match the system prompt in nearly every chat, making the search useless; deliberately widening `SEARCHED_TYPES` (e.g. to `agent` narration) is a one-line change but should be made on purpose.
- A query shorter than `MIN_QUERY` (2 chars) returns an empty result set with `ok: True` rather than an error — an empty search box is a normal UI state, not a failure.
- A malformed `document`/`log`/`logs` shape (e.g. `log` is a string rather than an object, which JSONB storage permits but the old file store would have failed at `json.loads` and skipped) is handled per-conversation: `_search_transcript` returns `(0, "")` for that one row rather than failing the whole search.
- The SQL `ILIKE` filter is a cheap pre-filter only ("if the term is nowhere in the document it cannot be in the conversation either"); the reverse does not hold, so every matching row is still walked through `_search_transcript` to confirm a real hit inside a searched entry type.
- Observed side-effect areas: none — read-only.
- Imported dependency areas include: `json`, `re`, `typing`, `helpers.persist_chat`, `helpers.api`, `helpers.db` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `db.transaction`, `conn.execute`, `content.casefold().find`, `content[start:end].split`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_chats_search.py`

## Child DOX Index

No child DOX files.
