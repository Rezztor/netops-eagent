# change_password.py DOX

## Purpose

- Own the `change_password.py` API endpoint.
- This module lets the signed-in user change their own password.
- Keep this file-level DOX profile synchronized with `change_password.py` because this directory is intentionally flat.

## Ownership

- `change_password.py` owns the runtime implementation.
- `change_password.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `_audit(event: str, **fields)`: emit an audit event via `plugins._audit.helpers.audit`, swallowing any failure so a logging problem never fails the password change itself.
- Classes:
- `ChangePassword` (`ApiHandler`)
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `ChangePassword` uses the `ApiHandler` defaults: `requires_auth()` True, `requires_csrf()` True, method `POST`, no `required_permission()` — any authenticated role may call it, since it only ever acts on the caller's own account.
- Re-verifies `current_password` against the signed-in user's own stored hash before accepting `new_password`, so a hijacked session cookie is not by itself enough to lock the real account holder out; `users.update_user` enforces the minimum length via `users.hash_password`.
- Emits `account.password_changed` on success, with `target=<username>` and `self_service=True`; distinguishes itself from an administrator-driven reset (`account.update` from `api/users_update.py`) by that flag.
- Sets `must_change_password=False`, since the holder has now chosen a password nobody else has seen.
- Observed side-effect areas: settings/state persistence (`users` table), secret handling.
- Imported dependency areas include: `typing`, `helpers.login`, `helpers.users`, `helpers.api`, `plugins._audit.helpers` (deferred, best-effort).
- A password change ends every session of the account; this handler re-issues the caller's own with `login.login_session`.

## Key Concepts

- Important called helpers/classes observed in the source: `login.current_user`, `users.verify_password`, `users.update_user`, `audit.emit`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_audit_logging.py`
  - `tests/test_legacy_ui_removal_readiness.py`
  - `tests/test_users_rbac.py`

## Child DOX Index

No child DOX files.
