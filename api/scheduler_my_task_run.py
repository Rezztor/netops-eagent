from helpers.api import ApiHandler, Input, Output, Request
from helpers.task_scheduler import TaskScheduler, TaskState, assert_task_owner
from helpers import settings
from helpers import user_scope


class SchedulerMyTaskRun(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        """
        Manually run one of the signed-in user's own reminders/schedules now.
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

        if task.state == TaskState.RUNNING:
            return {
                "error": "This is already running.",
                "task": scheduler.serialize_task(task_id),
            }

        try:
            await scheduler.run_task_by_uuid(task_id)
            return {
                "success": True,
                "message": f"'{task.name}' started.",
                "task": scheduler.serialize_task(task_id),
            }
        except ValueError as e:
            return {"error": str(e)}
