# scheduler_task_delete.py DOX

## Purpose

- Own the `scheduler_task_delete.py` API endpoint.
- This module handles scheduler task delete requests.
- Keep this file-level DOX profile synchronized with `scheduler_task_delete.py` because this directory is intentionally flat.

## Ownership

- `scheduler_task_delete.py` owns the runtime implementation.
- `scheduler_task_delete.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `SchedulerTaskDelete` (`ApiHandler`)
  - `async process(self, input: Input, request: Request) -> Output`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `SchedulerTaskDelete` is an `ApiHandler`.
- `SchedulerTaskDelete` defines `process(...)`.
- Observed side-effect areas: filesystem writes, filesystem deletion, settings/state persistence, scheduler state.
- Imported dependency areas include: `agent`, `helpers`, `helpers.api`, `helpers.localization`, `helpers.task_scheduler`.
- Only the task's owner or an administrator (`settings.manage`) may delete it (`assert_task_access`); anyone else gets the not-found answer.
- The task's conversation is resolved, reset and removed as the task's owner (`user_scope.user_scope(task.owner_id)`, `is_owned_by(task.owner_id)`, and `assert_context_access` under that scope before removal), never as the session user: an administrator deleting another account's task that had run used to get "Context not found". A conversation the owner does not hold is left alone. `tests/test_scheduler_admin_task_delete.py` covers this.

## Key Concepts

- Important called helpers/classes observed in the source: `scheduler.get_task_by_uuid`, `settings.apply_browser_timezone`, `scheduler.reload`, `AgentContext.get`, `user_scope.user_scope`, `scheduler.cancel_running_task`, `AgentContext.remove`, `persist_chat.remove_chat`, `scheduler.remove_task_by_uuid`, `context.reset`, `scheduler.update_task`, `scheduler.save`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- No direct test reference was found by name search; choose the nearest behavioral test or perform a focused smoke check.

## Child DOX Index

No child DOX files.
