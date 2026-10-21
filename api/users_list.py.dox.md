# users_list.py DOX

## Purpose

- Own the `users_list.py` API endpoint.
- This module lists all accounts and the available roles, administrators only.
- Keep this file-level DOX profile synchronized with `users_list.py` because this directory is intentionally flat.

## Ownership

- `users_list.py` owns the runtime implementation.
- `users_list.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `UsersList` (`ApiHandler`)
  - `get_methods(cls) -> list[str]` (`["GET", "POST"]`)
  - `required_permission(cls) -> str | None`
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Administrator-only: `required_permission()` returns `rbac.PERM_USERS_MANAGE`. Accepts both `GET` and `POST`; `requires_csrf()` follows `requires_auth()`'s default (`True`), so a `POST` call still needs the CSRF header even though the endpoint is read-only.
- Returns every account via `helpers.users.list_users()` (already the public projection, no password hashes) and the role catalogue via `helpers.rbac.list_roles()`, so the frontend can render both the user table and the role picker from one round trip.
- Read-only: emits no audit event and mutates nothing.
- Observed side-effect areas: none — read-only.
- Imported dependency areas include: `typing`, `helpers.rbac`, `helpers.users`, `helpers.api`.

## Key Concepts

- Important called helpers/classes observed in the source: `users.list_users`, `rbac.list_roles`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_users_rbac.py`

## Child DOX Index

No child DOX files.
