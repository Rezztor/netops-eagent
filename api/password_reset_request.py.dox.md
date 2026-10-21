# password_reset_request.py DOX

## Purpose

- Own the `password_reset_request.py` API endpoint.
- This module starts a password reset by mailing a reset link; unauthenticated by definition, since the caller is a locked-out user with no session.
- Keep this file-level DOX profile synchronized with `password_reset_request.py` because this directory is intentionally flat.

## Ownership

- `password_reset_request.py` owns the runtime implementation.
- `password_reset_request.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- Classes:
- `PasswordResetRequest` (`ApiHandler`)
  - `requires_auth(cls) -> bool` (`False`)
  - `requires_csrf(cls) -> bool` (`False`)
  - `async process(self, input: dict, request: Request) -> dict | Response`
- Module-level data: `_ANSWER`, the single fixed success payload returned regardless of outcome.

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Both `requires_auth()` and `requires_csrf()` are explicitly `False` — the caller cannot sign in, so there is no session to protect and no CSRF token to carry.
- **Always returns the identical `_ANSWER` payload**, regardless of whether an account was found, has no address on file, mail is disabled, or the send failed — answering differently would turn this into a membership oracle, letting a caller learn which of a list of identifiers hold accounts here. This is enforced by `helpers.password_reset.send_link`'s contract, and this handler must never branch its own response on the returned outcome string.
- **Answers before doing anything**: the lookup, the send and the audit event run on a worker through `password_reset.send_link_later`, so the reply time does not depend on whether the identifier exists. `tests/test_password_reset_timing.py` covers this.
- A source address (the peer and its `X-Forwarded-For`, see `helpers/password_reset.py.dox.md`) over `password_reset.address_may_request`'s allowance is answered 429 `{"ok": false, "error": ...}` before anything is looked up; the answer is the same whatever was named, so it says nothing about any account. `tests/test_password_reset_timing.py`.
- What actually happened (`"sent"`, `"no_such_account"`, `"disabled"`, `"rate_limited"`, `"no_address"`, `"no_base_url"`, `"send_failed"`, `"empty"`) goes only to the audit log as `password_reset.requested` with `outcome=<value>, via="api"` — visible to an administrator, not to the caller.
- The reset link's base URL comes from `helpers.mailer.config()["base_url"]` (the `smtp_base_url` setting), never inferred from the request's `Host` header, since that header is attacker-controlled and a link built from it could point at a server the attacker chose.
- `tests/test_api_schema_and_bootstrap.py::test_unauthenticated_endpoints_are_gated_some_other_way` asserts this endpoint is deliberately unauthenticated and is gated another way (rate-limiting per account and per source address, plus the constant-response design that makes it useless as an oracle).
- Observed side-effect areas: network calls (SMTP, via `helpers.password_reset.send_link` → `helpers.mailer`), settings/state persistence (`password_resets` table).
- Imported dependency areas include: `typing`, `helpers.mailer`, `helpers.password_reset`, `helpers.api`, `plugins._audit.helpers` (deferred, best-effort).

## Key Concepts

- Important called helpers/classes observed in the source: `password_reset.send_link_later`, `mailer.config`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Never make this handler's response vary by outcome; any new information about what happened belongs in the audit event, not the reply.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_api_schema_and_bootstrap.py`
  - `tests/test_member_mail.py`

## Child DOX Index

No child DOX files.
