# scheduler_my_task_delete.py DOX

## Purpose

- Own the `scheduler_my_task_delete.py` API endpoint.
- This module deletes one of the signed-in user's own reminders/schedules -- the personal, owner-checked counterpart to `scheduler_task_delete.py` (instance-wide, `scheduler.manage`-gated), mirroring its running-task and dedicated-context cleanup with an added ownership check up front.
- Keep this file-level DOX profile synchronized with `scheduler_my_task_delete.py` because this directory is intentionally flat.

## Ownership

- `scheduler_my_task_delete.py` owns the runtime implementation.
- `scheduler_my_task_delete.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `SchedulerMyTaskDelete` (`ApiHandler`)
  - `async process(self, input: Input, request: Request) -> Output`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `SchedulerMyTaskDelete` is an `ApiHandler`.
- `SchedulerMyTaskDelete` defines `process(...)`.
- Observed side-effect areas: filesystem writes, filesystem deletion, settings/state persistence, scheduler state.
- Imported dependency areas include: `agent`, `helpers`, `helpers.api`, `helpers.context_utils`, `helpers.localization`, `helpers.task_scheduler`, `helpers.user_scope`.
- Gated in `helpers/api_policy.py` on `PERM_CHAT_USE`, not `PERM_SCHEDULER_MANAGE` -- safe to open to every signed-in account only because `assert_task_owner` runs before any read of `task.context_id` or removal.

## Key Concepts

- Important called helpers/classes observed in the source: `settings.apply_browser_timezone`, `user_scope.require_current_user_id`, `TaskScheduler.get`, `scheduler.reload`, `scheduler.get_task_by_uuid`, `assert_task_owner`, `self.use_context`, `scheduler.cancel_running_task`, `context.reset`, `scheduler.update_task`, `scheduler.save`, `assert_context_access`, `AgentContext.remove`, `persist_chat.remove_chat`, `scheduler.remove_task_by_uuid`.
- Two ownership checks layer here: `assert_task_owner` for the task itself, then `assert_context_access` (from `helpers.context_utils`, the same guard `scheduler_task_delete.py` uses) before touching the task's dedicated `AgentContext` -- a second resource with its own ownership concept.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.
- Call `assert_task_owner(task, owner_id)` immediately after `get_task_by_uuid`, before any other read or mutation -- including before resolving `task.context_id`.

## Verification

- `tests/test_scheduler_my_tasks.py::test_delete_removes_an_owned_task`, `test_delete_refuses_someone_elses_task`.
- `tests/test_user_isolation.py::test_every_personal_scheduler_api_checks_task_ownership` -- source-level sweep that this file (and its siblings) call `assert_task_owner`.

## Child DOX Index

No child DOX files.
