# rbac.py DOX

## Purpose

- Own the `rbac.py` helper module.
- This module declares the permission and role vocabulary for Rezztor EAgent, so every enforcement point in the app asks this module rather than testing role name strings directly.
- Keep this file-level DOX profile synchronized with `rbac.py` because this directory is intentionally flat.

## Ownership

- `rbac.py` owns the runtime implementation.
- `rbac.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
  - `is_valid_role(role: str | None) -> bool`
  - `normalize_role(role: str | None) -> str`: coerce an unknown/missing role to `DEFAULT_ROLE`, never raising.
  - `get_permissions(role: str | None) -> set[str]`
  - `has_permission(role: str | None, permission: str) -> bool`
  - `has_all_permissions(role: str | None, permissions: Iterable[str]) -> bool`
  - `is_admin(role: str | None) -> bool`
  - `list_roles() -> list[dict]`: role catalogue for the admin UI (id, title, description, sorted permissions).
- Constants (permissions): `PERM_USERS_MANAGE`, `PERM_SETTINGS_MANAGE`, `PERM_PLUGINS_MANAGE`, `PERM_SCHEDULER_MANAGE`, `PERM_AUDIT_VIEW_ALL`, `PERM_CHAT_USE`, `PERM_TOOL_CODE_EXECUTION`, `PERM_TOOL_SUBORDINATES`, `PERM_TOOL_NETWORK`, `PERM_NETWORK_APPROVE`, `PERM_NETWORK_VIEW`, `PERM_INVENTORY_VIEW`, `PERM_INVENTORY_MANAGE`, `PERM_INVENTORY_APPROVE`, `PERM_INVENTORY_PLACES`, aggregated in `ALL_PERMISSIONS`.
- Constants (roles): `ROLE_ADMIN`, `ROLE_POWER`, `ROLE_USER`, the `ROLES` mapping (title, description, permission set per role), and `DEFAULT_ROLE = ROLE_USER`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Adding a role is meant to be a data change in `ROLES`, not a code change elsewhere — callers should always go through `has_permission`/`is_admin`/`normalize_role` rather than comparing role strings.
- `normalize_role` never raises and never widens access: an unrecognised role collapses to `DEFAULT_ROLE` (`ROLE_USER`, the least-privileged role), so a corrupt or stale stored role can only ever lose privilege, never gain it.
- `ROLE_ADMIN` implicitly holds `ALL_PERMISSIONS` (`set(ALL_PERMISSIONS)`), so a permission added to `ALL_PERMISSIONS` is automatically granted to every administrator without a separate edit to the admin role entry.
- `ROLE_POWER` explicitly excludes `PERM_USERS_MANAGE`, `PERM_SETTINGS_MANAGE`, `PERM_PLUGINS_MANAGE` and `PERM_AUDIT_VIEW_ALL`. It holds `PERM_NETWORK_APPROVE` (the network engineer approves the changes they request, ADR 0039); `ROLE_USER` holds neither it nor `PERM_TOOL_NETWORK`. `ROLE_POWER` also holds all three `PERM_INVENTORY_*` permissions (the engineer keeps the inventory and may approve their own proposals, ADR 0040); `ROLE_USER` holds none. `PERM_INVENTORY_PLACES` (add and rename sites, locations and buildings; delete or adopt any place) is held by administrators only, through `ALL_PERMISSIONS`.
- This module is the source of truth consulted by `helpers/api_policy.py` (per-endpoint gating), `helpers/tool_permissions.py` (per-tool gating), and `helpers/oidc.py` (`resolve_role`, group-to-role mapping).
- Observed side-effect areas: none — the module is pure (static tables and set operations).
- Imported dependency areas include: `typing`.

## Key Concepts

- Important called helpers/classes observed in the source: `set(ALL_PERMISSIONS)`, `ROLES[normalize_role(role)]`, `all(p in granted for p in permissions)`.
- Coarse-grained by design: fine enough to gate the things that matter (accounts, settings, plugins, tools, network changes), coarse enough that the permission list is reviewable in one sitting.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- A new sensitive capability should get its own `PERM_*` constant added to `ALL_PERMISSIONS`, then be assigned to the roles that should hold it and referenced from `helpers/api_policy.py` and/or `helpers/tool_permissions.py` as appropriate.
- Never gate on a bare role-name string comparison; always go through this module's functions so `normalize_role`'s fail-safe behavior applies.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_api_schema_and_bootstrap.py`
  - `tests/test_frontend_contract.py`
  - `tests/test_licence_client.py`
  - `tests/test_member_mail.py`
  - `tests/test_settings_sections.py`
  - `tests/test_tool_permissions.py`
  - `tests/test_users_rbac.py`

## Child DOX Index

No child DOX files.
