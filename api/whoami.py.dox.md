# whoami.py DOX

## Purpose

- Own the `whoami.py` API endpoint.
- This module returns the identity, role, and permission set of the signed-in user, so the WebUI can gate controls it should not render.
- Keep this file-level DOX profile synchronized with `whoami.py` because this directory is intentionally flat.

## Ownership

- `whoami.py` owns the runtime implementation.
- `whoami.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `WhoAmI` (`ApiHandler`)
  - `get_methods(cls) -> list[str]` (`["GET", "POST"]`)
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Uses the `ApiHandler` defaults: `requires_auth()` True, `requires_csrf()` True, no `required_permission()` — any authenticated role may call it, since it only ever describes the caller's own identity.
- Listed in `helpers/api.py`'s `PASSWORD_CHANGE_EXEMPT_ENDPOINTS` alongside `change_password`, `csrf_token`, `bootstrap`, and `logout` (per `helpers/users.py`'s comment on `ensure_seed_admin`), so a user whose account is flagged `must_change_password` can still call this to be told so and be shown the change screen — without that exemption the account would be locked out before it could reach the screen that unlocks it.
- Returns `{"ok": False, "error": "Not authenticated."}` rather than raising when `login.current_user()` is `None`.
- `role` and `role_title` come from `rbac.normalize_role`/`rbac.ROLES`, so an unrecognised stored role is reported as the least-privileged role rather than surfacing raw, possibly-invalid data.
- `permissions` is the full, sorted permission set for the resolved role (`rbac.get_permissions`), which is what the frontend uses to hide or disable controls the account cannot use — this is a UI convenience only, not the enforcement point; the server-side checks in `helpers/api_policy.py` and `helpers/tool_permissions.py` are authoritative regardless of what the client renders.
- Observed side-effect areas: none — read-only.
- Imported dependency areas include: `typing`, `helpers.login`, `helpers.rbac`, `helpers.api`.

## Key Concepts

- Important called helpers/classes observed in the source: `login.current_user`, `rbac.normalize_role`, `rbac.ROLES`, `rbac.get_permissions`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_legacy_ui_removal_readiness.py`
  - `tests/test_users_rbac.py`

## Child DOX Index

No child DOX files.
