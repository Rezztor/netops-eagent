# scheduler_my_task_resume.py DOX

## Purpose

- Own the `scheduler_my_task_resume.py` API endpoint.
- This module resumes one of the signed-in user's own paused reminders/schedules by setting its state back to `IDLE`, and only when it is currently `DISABLED` -- an idle, running, or errored task is left alone rather than reset out from under a state "resume" was never meant to touch.
- Keep this file-level DOX profile synchronized with `scheduler_my_task_resume.py` because this directory is intentionally flat.

## Ownership

- `scheduler_my_task_resume.py` owns the runtime implementation.
- `scheduler_my_task_resume.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `SchedulerMyTaskResume` (`ApiHandler`)
  - `async process(self, input: Input, request: Request) -> Output`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `SchedulerMyTaskResume` is an `ApiHandler`.
- `SchedulerMyTaskResume` defines `process(...)`.
- Observed side-effect areas: scheduler state (`state` -> `IDLE`, only from `DISABLED`).
- Imported dependency areas include: `helpers.api`, `helpers.localization`, `helpers.task_scheduler`, `helpers.user_scope`.
- Gated in `helpers/api_policy.py` on `PERM_CHAT_USE`, not `PERM_SCHEDULER_MANAGE` -- safe to open to every signed-in account only because `assert_task_owner` runs before `update_task`.

## Key Concepts

- Important called helpers/classes observed in the source: `settings.apply_browser_timezone`, `user_scope.require_current_user_id`, `TaskScheduler.get`, `scheduler.reload`, `scheduler.get_task_by_uuid`, `assert_task_owner`, `scheduler.update_task`, `serialize_task`.
- The `state != DISABLED` guard is deliberate: it is the mirror image of `scheduler_my_task_pause.py` and must stay a no-op-with-message rather than silently forcing `IDLE` on a `RUNNING` or `ERROR` task.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.
- Call `assert_task_owner(task, owner_id)` immediately after `get_task_by_uuid`, before any other read or mutation.

## Verification

- `tests/test_scheduler_my_tasks.py::test_resume_clears_a_paused_task`, `test_resume_refuses_a_task_that_is_not_paused`, `test_resume_refuses_someone_elses_task`.
- `tests/test_user_isolation.py::test_every_personal_scheduler_api_checks_task_ownership` -- source-level sweep that this file (and its siblings) call `assert_task_owner`.

## Child DOX Index

No child DOX files.
