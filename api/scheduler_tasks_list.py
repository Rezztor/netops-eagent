from helpers.api import ApiHandler, Input, Output, Request
from helpers.task_scheduler import TaskScheduler, can_access_task, serialize_tasks
from helpers import user_scope
import traceback
from helpers.print_style import PrintStyle
from helpers import settings


class SchedulerTasksList(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        """
        List the tasks the caller may manage: their own, or all for an administrator
        """
        try:
            # Get timezone from input (do not set if not provided, we then rely on poll() to set it)
            if timezone := input.get("timezone", None):
                settings.apply_browser_timezone(timezone)

            # Get task scheduler
            scheduler = TaskScheduler.get()
            await scheduler.reload()

            # The caller's own tasks; an administrator sees everyone's.
            caller = user_scope.get_current_user_id()
            tasks_list = serialize_tasks(
                [task for task in scheduler.get_tasks() if can_access_task(task, caller)]
            )

            return {"ok": True, "tasks": tasks_list}

        except Exception as e:
            PrintStyle.error(f"Failed to list tasks: {str(e)} {traceback.format_exc()}")
            # The trace is for the log above; the reply gets the message alone.
            return {"ok": False, "error": f"Failed to list tasks: {str(e)}", "tasks": []}
