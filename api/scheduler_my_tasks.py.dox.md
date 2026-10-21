# scheduler_my_tasks.py DOX

## Purpose

- Own the `scheduler_my_tasks.py` API endpoint.
- This module lists the signed-in user's own reminders and schedules -- the personal, owner-filtered counterpart to `scheduler_tasks_list.py` (instance-wide, `scheduler.manage`-gated).
- Keep this file-level DOX profile synchronized with `scheduler_my_tasks.py` because this directory is intentionally flat.

## Ownership

- `scheduler_my_tasks.py` owns the runtime implementation.
- `scheduler_my_tasks.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `SchedulerMyTasks` (`ApiHandler`)
  - `async process(self, input: Input, request: Request) -> Output`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `SchedulerMyTasks` is an `ApiHandler`.
- `SchedulerMyTasks` defines `process(...)`.
- Observed side-effect areas: none -- read-only; `scheduler.reload()` re-reads `usr/scheduler/tasks.json` but writes nothing.
- Imported dependency areas include: `helpers.api`, `helpers.localization`, `helpers.task_scheduler`, `helpers.user_scope`.
- Gated in `helpers/api_policy.py` on `PERM_CHAT_USE`, not `PERM_SCHEDULER_MANAGE` -- safe to open to every signed-in account only because every returned task is filtered by `task.owner_id == owner_id` inside this handler, never by a client-supplied id.

## Key Concepts

- Important called helpers/classes observed in the source: `settings.apply_browser_timezone`, `user_scope.require_current_user_id`, `TaskScheduler.get`, `scheduler.reload`, `scheduler.get_tasks`, `serialize_tasks`.
- `user_scope.require_current_user_id()` raises rather than returning `None`, so an unresolvable owner fails the request instead of silently listing nothing -- or, if the filter were ever dropped, everything.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.
- Do not add a client-supplied `owner_id`/`user_id` input to this handler -- the whole point is that the filter comes from the session, not the request body.

## Verification

- `tests/test_scheduler_my_tasks.py::test_list_returns_only_the_callers_own_tasks` -- two owners plus an ownerless task, asserts the response contains only the caller's.
- `tests/test_user_isolation.py::test_the_personal_task_list_filters_by_owner_not_by_a_client_supplied_id` -- source-level guard that the owner filter and `require_current_user_id` stay in this file.

## Child DOX Index

No child DOX files.
