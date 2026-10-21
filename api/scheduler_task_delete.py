from helpers.context_utils import assert_context_access
from helpers.api import ApiHandler, Input, Output, Request
from helpers.task_scheduler import TaskScheduler, TaskState, assert_task_access
from helpers import settings, user_scope
from agent import AgentContext
from helpers import persist_chat


class SchedulerTaskDelete(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        """
        Delete a task from the scheduler by ID
        """
        # Get timezone from input (do not set if not provided, we then rely on poll() to set it)
        if timezone := input.get("timezone", None):
            settings.apply_browser_timezone(timezone)

        scheduler = TaskScheduler.get()
        await scheduler.reload()

        # Get task ID from input
        task_id: str = input.get("task_id", "")

        if not task_id:
            return {"error": "Missing required field: task_id"}

        # Check if the task exists first
        task = scheduler.get_task_by_uuid(task_id)
        if not task:
            return {"error": f"Task with ID {task_id} not found"}
        assert_task_access(task)

        # The task's conversation belongs to the task's owner, not to the caller:
        # an administrator deleting someone else's task resolves it as that
        # owner. Resolving it as the session user answered "Context not found"
        # for every task that had run. Only a conversation the owner holds is
        # touched.
        with user_scope.user_scope(task.owner_id):
            context = AgentContext.get(task.context_id) if task.context_id else None
        if context and not context.is_owned_by(task.owner_id):
            context = None

        # If the task is running, update its state to IDLE first
        if task.state == TaskState.RUNNING:
            scheduler.cancel_running_task(task_id, terminate_thread=True)
            if context:
                with user_scope.user_scope(task.owner_id):
                    context.reset()
            # Update the state to IDLE so any ongoing processes know to terminate
            await scheduler.update_task(task_id, state=TaskState.IDLE)
            # Force a save to ensure the state change is persisted
            await scheduler.save()

        # This is a dedicated context for the task, so we remove it
        if context and context.id == task.uuid:
            with user_scope.user_scope(task.owner_id):
                assert_context_access(context.id)  # the chokepoint, as the owner
                AgentContext.remove(context.id)
                persist_chat.remove_chat(context.id)

        # Remove the task
        await scheduler.remove_task_by_uuid(task_id)

        return {"success": True, "message": f"Task {task_id} deleted successfully"}
