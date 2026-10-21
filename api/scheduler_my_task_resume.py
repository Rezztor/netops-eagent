from helpers.api import ApiHandler, Input, Output, Request
from helpers.task_scheduler import TaskScheduler, TaskState, serialize_task, assert_task_owner
from helpers import settings
from helpers import user_scope


class SchedulerMyTaskResume(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        """
        Resume one of the signed-in user's own paused reminders/schedules.

        Only a DISABLED task is resumed. Anything else (idle, running, or
        errored) is left alone rather than reset out from under a state that
        "resume" was never meant to touch.
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

        if task.state != TaskState.DISABLED:
            return {"error": f"'{task.name}' is not paused."}

        updated = await scheduler.update_task(task_id, state=TaskState.IDLE)
        if not updated:
            return {"error": f"Task with ID '{task_id}' could not be resumed"}

        return {"ok": True, "task": serialize_task(updated)}
