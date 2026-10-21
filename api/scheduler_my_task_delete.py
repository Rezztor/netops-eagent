from helpers.context_utils import assert_context_access
from helpers.api import ApiHandler, Input, Output, Request
from helpers.task_scheduler import TaskScheduler, TaskState, assert_task_owner
from helpers import settings
from agent import AgentContext
from helpers import persist_chat, user_scope


class SchedulerMyTaskDelete(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        """
        Delete one of the signed-in user's own reminders/schedules.

        Mirrors scheduler_task_delete's running-task and dedicated-context
        cleanup, with an added ownership check up front -- this endpoint is
        reachable by any signed-in account, not just scheduler.manage holders.
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
            return {"error": f"Task with ID {task_id} not found"}
        assert_task_owner(task, owner_id)

        context = None
        if task.context_id:
            context = self.use_context(task.context_id)

        if task.state == TaskState.RUNNING:
            scheduler.cancel_running_task(task_id, terminate_thread=True)
            if context:
                context.reset()
            await scheduler.update_task(task_id, state=TaskState.IDLE)
            await scheduler.save()

        if context and context.id == task.uuid:
            assert_context_access(context.id)
            AgentContext.remove(context.id)
            persist_chat.remove_chat(context.id)

        await scheduler.remove_task_by_uuid(task_id)

        return {"success": True, "message": f"'{task.name}' deleted."}
