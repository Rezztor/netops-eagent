# scheduler_my_task_run.py DOX

## Purpose

- Own the `scheduler_my_task_run.py` API endpoint.
- This module manually runs one of the signed-in user's own reminders/schedules now -- the personal, owner-checked counterpart to `scheduler_task_run.py` (instance-wide, `scheduler.manage`-gated).
- Keep this file-level DOX profile synchronized with `scheduler_my_task_run.py` because this directory is intentionally flat.

## Ownership

- `scheduler_my_task_run.py` owns the runtime implementation.
- `scheduler_my_task_run.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `SchedulerMyTaskRun` (`ApiHandler`)
  - `async process(self, input: Input, request: Request) -> Output`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `SchedulerMyTaskRun` is an `ApiHandler`.
- `SchedulerMyTaskRun` defines `process(...)`.
- Observed side-effect areas: scheduler state (starts a task run).
- Imported dependency areas include: `helpers.api`, `helpers.localization`, `helpers.task_scheduler`, `helpers.user_scope`.
- Gated in `helpers/api_policy.py` on `PERM_CHAT_USE`, not `PERM_SCHEDULER_MANAGE` -- safe to open to every signed-in account only because `assert_task_owner` runs before `run_task_by_uuid`, and raises `TaskAccessDenied` (mapped to 404 in `helpers/api.py`) for a task belonging to someone else.

## Key Concepts

- Important called helpers/classes observed in the source: `settings.apply_browser_timezone`, `user_scope.require_current_user_id`, `TaskScheduler.get`, `scheduler.reload`, `scheduler.get_task_by_uuid`, `assert_task_owner`, `scheduler.run_task_by_uuid`, `scheduler.serialize_task`.
- `run_task_by_uuid` already raises `ValueError` for a `DISABLED` task and resets `ERROR` to `IDLE` before running -- this handler only pre-checks `RUNNING` itself and otherwise lets that method's own guards and errors surface as `{"error": ...}`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.
- Call `assert_task_owner(task, owner_id)` immediately after `get_task_by_uuid`, before any other read or mutation.

## Verification

- `tests/test_scheduler_my_tasks.py::test_run_starts_an_owned_task`, `test_run_refuses_someone_elses_task`, `test_run_refuses_when_already_running`, `test_run_requires_a_task_id`.
- `tests/test_user_isolation.py::test_every_personal_scheduler_api_checks_task_ownership` -- source-level sweep that this file (and its siblings) call `assert_task_owner`.

## Child DOX Index

No child DOX files.
