# users_update.py DOX

## Purpose

- Own the `users_update.py` API endpoint.
- This module changes an account's role, password, profile fields, or enabled state, administrators only.
- Keep this file-level DOX profile synchronized with `users_update.py` because this directory is intentionally flat.

## Ownership

- `users_update.py` owns the runtime implementation.
- `users_update.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `_audit(event: str, **fields)`: emit an audit event via `plugins._audit.helpers.audit`, swallowing any failure.
- Classes:
- `UsersUpdate` (`ApiHandler`)
  - `required_permission(cls) -> str | None`
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Administrator-only: `required_permission()` returns `rbac.PERM_USERS_MANAGE`. Uses the `ApiHandler` defaults otherwise: `requires_auth()` True, `requires_csrf()` True, method `POST`.
- Requires `username`; every other field (`role`, `password`, `generate_password`, `display_name`, `email`, `disabled`) is optional and only the fields present are changed, via `helpers.users.update_user`'s keyword-only, `None`-means-unchanged contract.
- If `generate_password` is truthy and no explicit `password` is given, one is generated via `users.generate_password()` and returned once as `generated_password` in the response.
- `must_change_password` is forced to `True` only when the administrator is resetting **someone else's** password (`resetting_someone_else`, computed by comparing normalized usernames of the target and the acting administrator) — an administrator resetting their own password through this endpoint does not re-arm the forced-change screen, matching self-service behavior in `api/change_password.py`.
- Last-administrator protection (refusing to demote or disable the sole remaining admin) is enforced inside `helpers.users.update_user`, not here; a `users.UserError` from any validation failure is caught and returned as `{"ok": False, "error": str(exc)}`.
- Emits `account.update` with `target`, `target_id`, `role`, `disabled`, `password_changed`, and `password_generated`.
- Observed side-effect areas: settings/state persistence (`users` table), secret handling.
- Imported dependency areas include: `typing`, `helpers.login`, `helpers.rbac`, `helpers.users`, `helpers.api`, `plugins._audit.helpers` (deferred, best-effort).
- Setting a password ends every session of that account. When the caller changed their own, their session is re-issued.

## Key Concepts

- Important called helpers/classes observed in the source: `login.current_username`, `users.generate_password`, `users.normalize_username`, `users.update_user`, `audit.emit`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_audit_logging.py`

## Child DOX Index

No child DOX files.
