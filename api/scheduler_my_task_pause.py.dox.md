# scheduler_my_task_pause.py DOX

## Purpose

- Own the `scheduler_my_task_pause.py` API endpoint.
- This module pauses one of the signed-in user's own reminders/schedules by setting its state to `DISABLED`, which `helpers.task_scheduler.SchedulerTaskList.get_due_tasks` and `TaskScheduler.run_task_by_uuid` already both treat as "do not fire" -- there is no separate scheduler-level pause flag to keep in sync.
- Keep this file-level DOX profile synchronized with `scheduler_my_task_pause.py` because this directory is intentionally flat.

## Ownership

- `scheduler_my_task_pause.py` owns the runtime implementation.
- `scheduler_my_task_pause.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `SchedulerMyTaskPause` (`ApiHandler`)
  - `async process(self, input: Input, request: Request) -> Output`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `SchedulerMyTaskPause` is an `ApiHandler`.
- `SchedulerMyTaskPause` defines `process(...)`.
- Observed side-effect areas: scheduler state (`state` -> `DISABLED`).
- Imported dependency areas include: `helpers.api`, `helpers.localization`, `helpers.task_scheduler`, `helpers.user_scope`.
- Gated in `helpers/api_policy.py` on `PERM_CHAT_USE`, not `PERM_SCHEDULER_MANAGE` -- safe to open to every signed-in account only because `assert_task_owner` runs before `update_task`.

## Key Concepts

- Important called helpers/classes observed in the source: `settings.apply_browser_timezone`, `user_scope.require_current_user_id`, `TaskScheduler.get`, `scheduler.reload`, `scheduler.get_task_by_uuid`, `assert_task_owner`, `scheduler.update_task`, `serialize_task`.
- Pausing does not interrupt a run already in progress; it only stops what happens next. There is no `terminate_thread`/`cancel_running_task` call here, unlike `scheduler_my_task_delete.py`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.
- Call `assert_task_owner(task, owner_id)` immediately after `get_task_by_uuid`, before any other read or mutation.

## Verification

- `tests/test_scheduler_my_tasks.py::test_pause_disables_an_owned_task`, `test_pause_refuses_someone_elses_task`.
- `tests/test_user_isolation.py::test_every_personal_scheduler_api_checks_task_ownership` -- source-level sweep that this file (and its siblings) call `assert_task_owner`.

## Child DOX Index

No child DOX files.
