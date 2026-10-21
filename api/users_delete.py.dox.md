# users_delete.py DOX

## Purpose

- Own the `users_delete.py` API endpoint.
- This module deletes an account, administrators only.
- Keep this file-level DOX profile synchronized with `users_delete.py` because this directory is intentionally flat.

## Ownership

- `users_delete.py` owns the runtime implementation.
- `users_delete.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `_audit(event: str, **fields)`: emit an audit event via `plugins._audit.helpers.audit`, swallowing any failure.
- Classes:
- `UsersDelete` (`ApiHandler`)
  - `required_permission(cls) -> str | None`
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Administrator-only: `required_permission()` returns `rbac.PERM_USERS_MANAGE`. Uses the `ApiHandler` defaults otherwise: `requires_auth()` True, `requires_csrf()` True, method `POST`.
- Requires `username` in the input; returns `{"ok": False, "error": "Username is required."}` if missing.
- All refusal logic (self-deletion, last-administrator deletion, unknown username) lives in `helpers.users.delete_user`, which also removes the account's private data directory; a `users.UserError` is caught and returned rather than propagating.
- Emits `account.delete` with `target=<normalized username>` on success.
- Observed side-effect areas: filesystem deletion (the account's `usr/users/<id>/` directory, via `helpers.users.delete_user`), settings/state persistence (`users` table).
- Imported dependency areas include: `typing`, `helpers.login`, `helpers.rbac`, `helpers.users`, `helpers.api`, `plugins._audit.helpers` (deferred, best-effort).

## Key Concepts

- Important called helpers/classes observed in the source: `login.current_username`, `users.delete_user`, `users.normalize_username`, `audit.emit`.
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
