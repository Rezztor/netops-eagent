# password_reset_confirm.py DOX

## Purpose

- Own the `password_reset_confirm.py` API endpoint.
- This module spends a password-reset token and sets a new password; unauthenticated by definition, since the caller is a locked-out user with no session.
- Keep this file-level DOX profile synchronized with `password_reset_confirm.py` because this directory is intentionally flat.

## Ownership

- `password_reset_confirm.py` owns the runtime implementation.
- `password_reset_confirm.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `_audit(event: str, **fields)`: emit an audit event via `plugins._audit.helpers.audit`, swallowing any failure.
- Classes:
- `PasswordResetConfirm` (`ApiHandler`)
  - `requires_auth(cls) -> bool` (`False`)
  - `requires_csrf(cls) -> bool` (`False`)
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Both `requires_auth()` and `requires_csrf()` are explicitly `False` — the caller cannot sign in, and CSRF protection needs a session-bound token the sign-in flow has no session to carry. This is the deliberate, narrow exception; the token itself (32 random bytes, single-use, short-lived) is the credential.
- All logic lives in `helpers.password_reset.complete`, shared verbatim with the server-rendered "choose a new password" page the emailed link points at, so the security-sensitive flow is not implemented twice.
- On success returns `{"ok": True, "message": ...}`; on failure `{"ok": False, "error": ...}` — the message differs (invalid/expired/already-used token, password too short), unlike `password_reset_request.py`, because a caller here already holds the one-time token and there is no membership-oracle risk in saying why it failed.
- Emits `password_reset.completed` or `password_reset.rejected` with `via="api"` for the audit log, regardless of which specific failure occurred.
- `tests/test_api_schema_and_bootstrap.py::test_unauthenticated_endpoints_are_gated_some_other_way` asserts this endpoint is deliberately unauthenticated and is gated by another mechanism (the single-use, time-limited token) rather than by a session check.
- Observed side-effect areas: settings/state persistence (`password_resets`/`users` tables), secret handling.
- Imported dependency areas include: `typing`, `helpers.password_reset`, `helpers.api`, `plugins._audit.helpers` (deferred, best-effort).

## Key Concepts

- Important called helpers/classes observed in the source: `password_reset.complete`, `audit.emit`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_api_schema_and_bootstrap.py`

## Child DOX Index

No child DOX files.
