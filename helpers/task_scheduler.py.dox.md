# task_scheduler.py DOX

## Purpose

- Own the `task_scheduler.py` helper module.
- This module models, serializes, schedules, runs, and persists scheduled tasks.
- Keep this file-level DOX profile synchronized with `task_scheduler.py` because this directory is intentionally flat.

## Ownership

- `task_scheduler.py` owns the runtime implementation.
- `task_scheduler.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `TaskState` (`str`, `Enum`)
- `TaskType` (`str`, `Enum`)
- `TaskSchedule` (`BaseModel`)
  - `to_crontab(self) -> str`
- `TaskPlan` (`BaseModel`)
  - `create(cls, todo: list[datetime] | None=..., in_progress: datetime | None=..., done: list[datetime] | None=...)`
  - `add_todo(self, launch_time: datetime)`
  - `set_in_progress(self, launch_time: datetime)`
  - `set_done(self, launch_time: datetime)`
  - `get_next_launch_time(self) -> datetime | None`
  - `should_launch(self) -> datetime | None`
- `BaseTask` (`BaseModel`)
  - `update(self, name: str | None=..., state: TaskState | None=..., system_prompt: str | None=..., prompt: str | None=..., attachments: list[str] | None=..., last_run: datetime | None=..., last_result: str | None=..., context_id: str | None=..., **kwargs)`
  - `check_schedule(self, frequency_seconds: float=...) -> bool`
  - `get_next_run(self) -> datetime | None`
  - `is_dedicated(self) -> bool`
  - `get_next_run_minutes(self) -> int | None`
  - `async on_run(self)`
  - `async on_finish(self)`
  - `async on_error(self, error: str)`
- `AdHocTask` (`BaseTask`)
  - `create(cls, name: str, system_prompt: str, prompt: str, token: str, attachments: list[str] | None=..., context_id: str | None=..., project_name: str | None=..., project_color: str | None=...)`
  - `update(self, name: str | None=..., state: TaskState | None=..., system_prompt: str | None=..., prompt: str | None=..., attachments: list[str] | None=..., last_run: datetime | None=..., last_result: str | None=..., context_id: str | None=..., token: str | None=..., **kwargs)`
- `ScheduledTask` (`BaseTask`)
  - `create(cls, name: str, system_prompt: str, prompt: str, schedule: TaskSchedule, attachments: list[str] | None=..., context_id: str | None=..., timezone: str | None=..., project_name: str | None=..., project_color: str | None=...)`
  - `update(self, name: str | None=..., state: TaskState | None=..., system_prompt: str | None=..., prompt: str | None=..., attachments: list[str] | None=..., last_run: datetime | None=..., last_result: str | None=..., context_id: str | None=..., schedule: TaskSchedule | None=..., **kwargs)`
  - `check_schedule(self, frequency_seconds: float=...) -> bool`
  - `get_next_run(self) -> datetime | None`
- `PlannedTask` (`BaseTask`)
  - `create(cls, name: str, system_prompt: str, prompt: str, plan: TaskPlan, attachments: list[str] | None=..., context_id: str | None=..., project_name: str | None=..., project_color: str | None=...)`
  - `update(self, name: str | None=..., state: TaskState | None=..., system_prompt: str | None=..., prompt: str | None=..., attachments: list[str] | None=..., last_run: datetime | None=..., last_result: str | None=..., context_id: str | None=..., plan: TaskPlan | None=..., **kwargs)`
  - `check_schedule(self, frequency_seconds: float=...) -> bool`
  - `get_next_run(self) -> datetime | None`
  - `async on_run(self)`
  - `async on_finish(self)`
  - `async on_success(self, result: str)`
  - `async on_error(self, error: str)`
- `SchedulerTaskList` (`BaseModel`)
  - `get(cls) -> 'SchedulerTaskList'`
  - `async reload(self) -> 'SchedulerTaskList'`
  - `async add_task(self, task: Union[ScheduledTask, AdHocTask, PlannedTask]) -> 'SchedulerTaskList'`
  - `async save(self) -> 'SchedulerTaskList'`
  - `reset_running(self) -> None`: puts tasks stored as RUNNING back to IDLE; called once, when `TaskScheduler` is first built in a process.
  - `async update_task_by_uuid(self, task_uuid: str, updater_func: Callable[[Union[ScheduledTask, AdHocTask, PlannedTask]], None], verify_func: Callable[[Union[ScheduledTask, AdHocTask, PlannedTask]], bool]=...) -> Union[ScheduledTask, AdHocTask, PlannedTask] | None`
  - `get_tasks(self) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]`
  - `get_tasks_by_context_id(self, context_id: str, only_running: bool=...) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]`
  - `async get_due_tasks(self) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]`
- `TaskAccessDenied` (`Exception`): raised when a caller acts on a task owned by someone else.
- `TaskScheduler` (no explicit base class)
  - `get(cls) -> 'TaskScheduler'`
  - `cancel_running_task(self, task_uuid: str, terminate_thread: bool=...) -> bool`
  - `cancel_tasks_by_context(self, context_id: str, terminate_thread: bool=...) -> bool`
  - `async reload(self)`
  - `get_tasks(self) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]`
  - `get_tasks_by_context_id(self, context_id: str, only_running: bool=...) -> list[Union[ScheduledTask, AdHocTask, PlannedTask]]`
  - `async add_task(self, task: Union[ScheduledTask, AdHocTask, PlannedTask]) -> 'TaskScheduler'`
  - `async remove_task_by_uuid(self, task_uuid: str) -> 'TaskScheduler'`
- Top-level functions:
- `normalize_schedule_timezone(timezone_name: str | None) -> str`
- `_now() -> datetime`
- `_localize_task_datetime(dt: datetime) -> datetime`
- `usable_attachments(task) -> list[str]`: the attachments a run hands the agent -- URLs as given, and file paths only once `workdir.confine_read_path(path, user_id=task.owner_id)` accepts them and the resolved path exists; an ownerless task gets no files.
- `_current_owner() -> str | None`: resolves `helpers.user_scope.get_current_user_id()` for `BaseTask.owner_id`'s `default_factory`; `None` outside a request or an owned agent run.
- `assert_task_owner(task, owner_id: str | None) -> None`: raises `TaskAccessDenied` unless `owner_id` matches `task.owner_id`, including when `task.owner_id` is `None` -- an ownerless task belongs to nobody's personal view. Mirrors `helpers.context_utils.assert_context_access`; used by every `api/scheduler_my_task_*.py` and `api/scheduler_my_tasks.py` handler (`helpers/api_policy.py`, `PERM_CHAT_USE`) so those are safe to open to any signed-in account instead of `PERM_SCHEDULER_MANAGE`.
- `serialize_datetime(dt: Optional[datetime]) -> Optional[str]`: Serialize a datetime object to ISO format string in the user's timezone.
- `parse_datetime(dt_str: Optional[str]) -> Optional[datetime]`: Parse ISO format datetime string with timezone awareness.
- `serialize_task_schedule(schedule: TaskSchedule) -> Dict[str, str]`: Convert TaskSchedule to a standardized dictionary format.
- `parse_task_schedule(schedule_data: Dict[str, str]) -> TaskSchedule`: Parse dictionary into TaskSchedule with validation.
- `serialize_task_plan(plan: TaskPlan) -> Dict[str, Any]`: Convert TaskPlan to a standardized dictionary format.
- `parse_task_plan(plan_data: Dict[str, Any]) -> TaskPlan`: Parse dictionary into TaskPlan with validation.
- `serialize_task(task: Union[ScheduledTask, AdHocTask, PlannedTask]) -> Dict[str, Any]`: Standardized serialization for task objects with proper handling of all complex types.
- `serialize_tasks(tasks: list[Union[ScheduledTask, AdHocTask, PlannedTask]]) -> list[Dict[str, Any]]`: Serialize a list of tasks to a list of dictionaries.
- `deserialize_task(task_data: Dict[str, Any], task_class: Optional[Type[T]]=...) -> T`: Deserialize dictionary into appropriate task object with validation.
- Notable constants/configuration names: `SCHEDULER_FOLDER`, `LOCAL_TIMEZONE_ALIASES`, `T`.

## Runtime Contracts

- A task runs as its owner, so `_run_task` (the job loop's tick and a run by hand both end there) starts nothing when `_owner_may_run(owner_id)` says no: the owner's account is disabled, no longer exists, or cannot be looked up. A task with no owner is the framework's own and runs. The task is left in place, so enabling the account brings it back. `forget_owner(owner_id)` drops a deleted account's tasks from memory; `helpers.users` deletes the rows. `tests/test_scheduler_owner_accounts.py` covers this.
- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: filesystem reads, filesystem writes, filesystem deletion, network calls, settings/state persistence, secret handling, scheduler state.
- Imported dependency areas include: `agent`, `asyncio`, `crontab`, `datetime`, `enum`, `helpers`, `helpers.defer`, `helpers.files`, `helpers.localization`, `helpers.persist_chat`, `helpers.print_style`, `initialize`, `nest_asyncio`, `os`, `os.path`, `pydantic`.
- A task belongs to its owner and runs as its owner. `can_access_task(task, user_id)` is true for the owner and for an administrator (`settings.manage`); `assert_task_access(task)` raises `TaskAccessDenied` for anyone else, answering as a missing task does. `scheduler.manage` lets an account keep schedules, not reach another account's. A task with no owner is an administrator's. `owner_id` is set at creation and no API or tool path changes it. `tests/test_scheduler_my_tasks.py` covers this.

- `SchedulerTaskList.save()` writes the rows and nothing else. Until 2026-10-16 it ended with a leftover file-era check on an undefined `path`, so every save raised `NameError` after committing: the job loop's RUNNING write escaped `_run_task_body` before its `try`, and the task stayed RUNNING for good. `TaskScheduler.__init__` calls `reset_running()` so rows left RUNNING by that bug, or by a process that died mid-run, are due again; a freshly built scheduler runs nothing, so every RUNNING row it sees is stale. One process owns the scheduler (no uvicorn workers); a second process building a `TaskScheduler` against the same database would reset the first one's live runs.
- `reload()` reads the rows and replaces the in-memory list under `_lock`, so a task another thread saves cannot fall between the read and the replace (and then be deleted by the next `_write_rows`).
- Attachments are confined at run time (`usable_attachments`), not at create/update: a stored path outside the owner's folders is skipped, and the run no longer reveals whether it exists.
- `tests/test_scheduler_save_and_recovery.py` runs the real `save()`, a job-loop run, the recovery and the attachment confinement against the database.

## Key Concepts

- Important called helpers/classes observed in the source: `nest_asyncio.apply`, `TypeVar`, `str.strip`, `callable`, `datetime.now`, `tzinfo.localize`, `Field`, `PrivateAttr`, `Localization.get.serialize_datetime`, `normalize_schedule_timezone`, `Localization.get.get_timezone`, `pytz.timezone`, `now`, `localize`, `cls`, `_localize_task_datetime`, `self.todo.remove`, `self.get_next_launch_time`, `super.__init__`, `threading.RLock`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_task_scheduler_timezone.py`
  - `tests/test_timezone_regressions.py`
  - `tests/test_tool_action_contracts.py`
  - `tests/test_scheduler_my_tasks.py`: `scheduler_my_*` handler behaviour against a stubbed `TaskScheduler.get()`.
  - `tests/test_user_isolation.py`: `assert_task_owner`/`TaskAccessDenied` ownership guard, plus a source-level sweep (`test_every_personal_scheduler_api_checks_task_ownership`) that every `api/scheduler_my_*.py` handler acting on a specific task id calls it.

## Child DOX Index

No child DOX files.
