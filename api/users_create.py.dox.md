# users_create.py DOX

## Purpose

- Own the `users_create.py` API endpoint.
- This module creates a new account with a role, administrators only.
- Keep this file-level DOX profile synchronized with `users_create.py` because this directory is intentionally flat.

## Ownership

- `users_create.py` owns the runtime implementation.
- `users_create.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `_audit(event: str, **fields)`: emit an audit event via `plugins._audit.helpers.audit`, swallowing any failure.
- Classes:
- `UsersCreate` (`ApiHandler`)
  - `required_permission(cls) -> str | None`
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Administrator-only: `required_permission()` returns `rbac.PERM_USERS_MANAGE`. `helpers/api_policy.py` lists `users_create` under the same permission as a reviewable backstop, but this handler's own classmethod is authoritative.
- Uses the `ApiHandler` defaults for everything else: `requires_auth()` True, `requires_csrf()` True, method `POST`.
- If no `password` is supplied, one is generated via `users.generate_password()` and returned once in the response as `generated_password` — never stored in plaintext and never retrievable again; `created_by` is the acting administrator's username, resolved via `helpers.login.current_username()`.
- All validation (username shape, role validity, email uniqueness/shape, password length) happens inside `helpers.users.create_user`; a `users.UserError` is caught and returned as `{"ok": False, "error": str(exc)}` rather than propagating.
- Emits `account.create` with `target`, `target_id`, `role`, and `password_generated` on success.
- Observed side-effect areas: settings/state persistence (`users` table), secret handling.
- Imported dependency areas include: `typing`, `helpers.login`, `helpers.rbac`, `helpers.users`, `helpers.api`, `plugins._audit.helpers` (deferred, best-effort).

## Key Concepts

- Important called helpers/classes observed in the source: `login.current_username`, `users.generate_password`, `users.create_user`, `audit.emit`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_audit_logging.py`
  - `tests/test_users_rbac.py`

## Child DOX Index

No child DOX files.
