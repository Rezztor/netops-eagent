from helpers.api import ApiHandler, Input, Output, Request
from helpers.task_scheduler import TaskScheduler, serialize_tasks
from helpers import settings
from helpers import user_scope


class SchedulerMyTasks(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        """
        List the signed-in user's own reminders and schedules.

        Unlike scheduler_tasks_list (instance-wide, scheduler.manage-gated),
        this only ever returns tasks whose owner_id matches the caller -- the
        filter that makes it safe to open to every signed-in account.
        """
        if timezone := input.get("timezone", None):
            settings.apply_browser_timezone(timezone)

        owner_id = user_scope.require_current_user_id()

        scheduler = TaskScheduler.get()
        await scheduler.reload()

        my_tasks = [task for task in scheduler.get_tasks() if task.owner_id == owner_id]

        return {"ok": True, "tasks": serialize_tasks(my_tasks)}
