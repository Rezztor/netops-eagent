# api_policy.py DOX

## Purpose

- Own the `api_policy.py` helper module.
- This module holds the central RBAC policy table mapping `/api/<path>` endpoint names to the permission required to call them.
- Keep this file-level DOX profile synchronized with `api_policy.py` because this directory is intentionally flat.

## Ownership

- `api_policy.py` owns the runtime implementation.
- `api_policy.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `_grant(permission: str, *endpoints: str) -> None`: internal helper that populates `API_PERMISSIONS`.
- `required_permission_for(path: str) -> str | None`: permission required for an `/api/<path>` endpoint, or `None` if any signed-in user may call it.
- Module-level data: `API_PERMISSIONS: dict[str, str]`, the endpoint-name -> permission table itself.
- Module-level data: `REFUSED_WHEN_RESTRICTED: frozenset[str]`, the endpoints the dispatcher answers 403 while the installation is restricted (no licence, or a licence past its grace): the device and Azure change approvals and the two manual scheduled-task runs. Agent runs and scheduled tasks are refused at their own chokepoints (`helpers/license_entitlements.py`); this list is for what starts something without going through either.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever an endpoint is added to or removed from `API_PERMISSIONS`, or a handler's own `required_permission()` changes and this table's comment needs to stay accurate.
- A handler's own `required_permission()` classmethod wins over this table when declared; this table is the fallback and the one place meant to be reviewed in a sitting for what is gated (see `helpers/rbac.py` for the permission constants).
- `required_permission_for(path)` strips leading/trailing `/` before lookup, so callers may pass either `"users_list"` or `"/users_list/"`.
- Currently grants: `rbac.PERM_SETTINGS_MANAGE` (settings, MCP servers, backup, cache reset, restart, and licence administration: `license_status` and `license_activate`, neither of which returns the installation token or the activation key), `rbac.PERM_USERS_MANAGE` (`users_list`, `users_create`, `users_update`, `users_delete`), `rbac.PERM_PLUGINS_MANAGE` (plugin/skill install and management endpoints), `rbac.PERM_CHAT_USE` (a small set of user-scoped endpoints that are already self-filtering by owner), `rbac.PERM_AUDIT_VIEW_ALL`, `rbac.PERM_NETWORK_VIEW` (`plugins/_ea_ansible/network_jobs`, and `network_list`, the appliance's networks read-only), `rbac.PERM_NETWORK_APPROVE` (`change_approve`, `changes_review`, `approvals_waiting` (the Approvals page), and the Azure approvals `plugins/_ea_azure_change/azure_approve` and `plugins/_ea_azure/azure_read_approve`), `rbac.PERM_TOOL_NETWORK` (Azure card edits and document downloads: `plugins/_ea_azure_change/azure_edit`, `azure_document`, `plugins/_ea_azure/azure_read_edit`; `azure_document` declares `network.approve` on the handler for another user's record), `plugins/_ea_azure_change/azure_change_config` (`plugins.manage`: guard rails, keys, reference pattern), `plugins/_ea_azure_change/azure_changes` (`inventory.view`), `rbac.PERM_SCHEDULER_MANAGE`, and the inventory permissions (`inventory.view`, including `plugins/_ea_inventory/inventory_summary`, `inventory_schedule`, `inventory_places`, `inventory_ports`, `inventory_vlan`, `inventory_backups`, `inventory_backup_report`, `inventory_drift` (the drift inbox) and the Azure reads `plugins/_ea_azure/azure_*` (`azure_cost` asks Azure Cost Management, cached four hours); `inventory.manage`, including `inventory_place_save` (the handler also requires `inventory.places` for sites, locations and buildings, any `chain` and every delete) and the observed-data actions `inventory_refresh_ports`, `inventory_port_uplink`, `inventory_backup_run`, `inventory_backup_view` and `inventory_backup_diff`, where the click is the approval; `inventory.approve`; `inventory.places` is declared by the `inventory_place_merge` handler itself, not in this table).
- Observed side-effect areas: none — the module is pure (a static table and a dict lookup).
- Imported dependency areas include: `helpers.rbac`.
- `scheduler.manage` on the `scheduler_task_*` endpoints is the outer gate only; each handler also checks the task's owner (`helpers.task_scheduler.assert_task_access`). `projects` is not in the table because it gates by action: create, clone, update and delete need `settings.manage` in the handler.

## Key Concepts

- Important called helpers/classes observed in the source: `API_PERMISSIONS.get`, `path.strip`.
- Each `_grant` call site carries an inline comment explaining *why* that endpoint needs that permission (e.g. why `settings_get` is administrator-only for reads too, why plugin routes are gated by dispatch path rather than directory) — read those comments before adding or removing an entry, since the reasoning is not always obvious from the permission name alone.
- The table's premise -- a missing gate is visible by its absence -- is enforced by `tests/test_api_permission_coverage.py`, not by habit. It asserts every endpoint is either gated here, declares `required_permission()`, or sits on a reviewed list of endpoints any signed-in account may call, so a newly added endpoint fails until somebody classifies it. The premise held structurally and was checked by nothing, which is how ten file-browser endpoints and the plaintext API-key reveal stayed reachable by the `chat.use` role.
- `subagents` is gated on `plugins.manage`: its save and delete actions rewrite `usr/agents`, the profiles every account's agent loads.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Adding a new sensitive endpoint means adding it to `API_PERMISSIONS` (or declaring `required_permission()` on the handler itself); an endpoint absent from both is reachable by any signed-in user.
- Gate plugin-provided endpoints by their full dispatch path (e.g. `plugins/_ea_ansible/network_jobs`), not by assuming the directory they live in implies a permission.
- Preserve public helper APIs used by core code and plugins unless every caller is updated.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_api_schema_and_bootstrap.py`
  - `tests/test_frontend_contract.py`
  - `tests/test_users_rbac.py`

## Child DOX Index

No child DOX files.
