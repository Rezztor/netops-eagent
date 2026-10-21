# logout.py DOX

## Purpose

- Own the `logout.py` API endpoint.
- This module clears login/session state for the current client, and first calls `users.end_sessions` for the signed-in account so every other copy of its session ends too (a copied cookie does not outlive the sign-out). A failure there is logged and the browser is still signed out. `tests/test_users_rbac.py::test_signing_out_ends_every_copy_of_the_session`.
- Keep this file-level DOX profile synchronized with `logout.py` because this directory is intentionally flat.

## Ownership

- `logout.py` owns the runtime implementation.
- `logout.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `ApiLogout` (`ApiHandler`)
  - `requires_auth(cls) -> bool`
  - `requires_csrf(cls) -> bool`
  - `async process(self, input: dict, request: Request) -> dict`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `ApiLogout` is an `ApiHandler`.
- `ApiLogout` defines `process(...)`.
- `ApiLogout` defines `requires_auth(...)`.
- Observed side-effect areas: secret handling.
- Imported dependency areas include: `helpers.api`.
- `ApiLogout` requires the CSRF header although it needs no authentication. It is the only sign-out: `GET /logout` redirects without signing out. The frontend's `SignOutButton` posts here.

## Key Concepts

- Important called helpers/classes observed in the source: `session.clear`, `session.pop`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_office_document_store.py`

## Child DOX Index

No child DOX files.
