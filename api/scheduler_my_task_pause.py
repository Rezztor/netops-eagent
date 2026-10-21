from helpers.api import ApiHandler, Input, Output, Request
from helpers.task_scheduler import TaskScheduler, TaskState, serialize_task, assert_task_owner
from helpers import settings
from helpers import user_scope


class SchedulerMyTaskPause(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        """
        Pause one of the signed-in user's own reminders/schedules.

        Sets state to DISABLED, which both get_due_tasks() and
        run_task_by_uuid() already treat as "do not fire" -- so this alone is
        enough to stop future automatic and manual runs. A run already in
        progress is not interrupted; pausing only affects what happens next.
        """
        if timezone := input.get("timezone", None):
            settings.apply_browser_timezone(timezone)

        task_id: str = input.get("task_id", "")
        if not task_id:
            return {"error": "Missing required field: task_id"}

        owner_id = user_scope.require_current_user_id()

        scheduler = TaskScheduler.get()
        await scheduler.reload()

        task = scheduler.get_task_by_uuid(task_id)
        if not task:
            return {"error": f"Task with ID '{task_id}' not found"}
        assert_task_owner(task, owner_id)

        updated = await scheduler.update_task(task_id, state=TaskState.DISABLED)
        if not updated:
            return {"error": f"Task with ID '{task_id}' could not be paused"}

        return {"ok": True, "task": serialize_task(updated)}
