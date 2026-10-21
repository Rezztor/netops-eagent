# tool_permissions.py DOX

## Purpose

- Own the `tool_permissions.py` helper module.
- This module maps agent tool names to the RBAC permission required to invoke them, and is the module that actually enforces `helpers/rbac.py`'s `tools.*` permissions at the point a tool is resolved by name.
- Keep this file-level DOX profile synchronized with `tool_permissions.py` because this directory is intentionally flat.

## Ownership

- `tool_permissions.py` owns the runtime implementation.
- `tool_permissions.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
  - `required_permission(tool_name: str) -> str | None`: permission needed for a tool name, or `None` if none is required beyond being signed in.
  - `is_tool_name(tool_name: str) -> bool`: true only for a plain tool name, `[A-Za-z0-9_-]+` and nothing else. Every shipped tool file name has that shape.
  - `is_allowed(tool_name: str, role: str | None) -> bool`: whether `role` may invoke `tool_name`. Anything that is not a plain tool name (`./code_execution_tool`, `x/../net_apply`, `/eagent/usr/users/<id>/uploads/evil`, `a.b`) is refused for every role: `Agent.get_tool` turns the name into a file path and imports it, so such a name would load the real tool while matching no entry in the map, or import any `.py` on disk. **`Agent.get_tool` asks this before it looks on disk** (audit 6 C1: the check used to run after the import, so an uploaded `.py` named as a tool ran in the web-server process). `refusal_message` says "not a tool name" for such a name rather than naming a role.
  - `role_for_context(context) -> str | None`: resolve the role of the account that **owns** the conversation (`context.owner_id`), not the current request session — because an agent run happens on a background thread that may outlive the request that started it.
  - `refusal_message(tool_name: str, role: str | None) -> str`: a message telling the model plainly that the tool is unavailable to this role, instructing it not to retry or work around the refusal.
- Module-level data: `TOOL_PERMISSIONS: dict[str, str]`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Enforcement point is `Agent.get_tool`, where a tool is resolved by name — not inside each tool implementation. A tool that itself forgets to check permissions is not a gap here, because the resolver refuses before the tool code runs and cannot be bypassed by adding a new tool.
- Tools **absent** from `TOOL_PERMISSIONS` require no permission beyond being signed in — this is the deliberate safe default, since the dangerous capabilities (code execution, browser, subordinates, network) are enumerable and few, while harmless tools change often and would otherwise need constant upkeep here.
- Currently gated: `code_execution_tool`, `code_execution_remote`, `input`, `text_editor`, `text_editor_remote`, `office_artifact`, `attach_file` (all `PERM_TOOL_CODE_EXECUTION`; `attach_file` because fetching a file from the runner claims the account a slot there); `call_subordinate`, `parallel` (`PERM_TOOL_SUBORDINATES`); `net_read`, `net_change`, `net_apply`, `net_plan`, `net_credential`, `solarwinds_lookup` (`PERM_TOOL_NETWORK` — note this only gates whether the network tools may be invoked at all; what actually runs against a device is separately decided by the Ansible change-approval gate, `rbac.PERM_NETWORK_APPROVE`); `inventory_lookup`, `inventory_ports` (`PERM_INVENTORY_VIEW`); `inventory_stage`, `inventory_read_file`, `inventory_refresh_facts` (`PERM_INVENTORY_MANAGE`; each only ever creates pending requests or conflicts); `azure_inventory`, `azure_advise`, `azure_cost` (`PERM_INVENTORY_VIEW`), `azure_pull` (`PERM_INVENTORY_MANAGE`); `azure_change`, `azure_apply`, `azure_action`, `azure_read` (`PERM_TOOL_NETWORK` — only whether the tools may be invoked; what runs in Azure is decided by the Azure change plugin's gate: engineer approval, a CR/SR number, an issued document; none of these tools can delete or stop anything, ADR 0065); `scheduler` (`PERM_SCHEDULER_MANAGE`).
- The system prompt still describes tools a user's role may not run — deliberately: a refusal message the model can read and explain to the user is considered more useful than a tool that silently does not exist.
- `role_for_context` swallows any lookup failure and returns `None`, which `is_allowed`/`has_permission` then resolve to the least-privileged behavior via `rbac.normalize_role`.
- Observed side-effect areas: none — reads user records via `helpers.users` but performs no writes.
- Imported dependency areas include: `helpers.rbac`, `helpers.users` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `rbac.has_permission`, `rbac.normalize_role`, `rbac.ROLES.get`, `users.get_user_by_id`, `getattr(context, "owner_id", None)`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- A new tool with a dangerous capability (code execution, browser/network egress, spawning further agents, unattended scheduling) must be added to `TOOL_PERMISSIONS`; anything else is intentionally left ungated.
- Resolve role from the conversation owner, never from the live request session, for any code path that may run on a background thread.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_tool_permissions.py`

## Child DOX Index

No child DOX files.
