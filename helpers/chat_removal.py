"""Remove a conversation: the cleanup shared by chat_remove and chat_remove_many.

Callers own the ownership check (`assert_context_access`) and the scheduler
reload / dirty marking, so a batch pays for those once rather than per id.
"""

from agent import AgentContext
from helpers import persist_chat
from helpers.task_scheduler import TaskScheduler


def remove_chat_state(ctxid: str) -> None:
    """Stop and delete one conversation. Does not reload the scheduler."""
    TaskScheduler.get().cancel_tasks_by_context(ctxid, terminate_thread=True)

    context = AgentContext.use(ctxid)
    if context:
        # stop processing any tasks
        context.reset()

    AgentContext.remove(ctxid)
    persist_chat.remove_chat(ctxid)


async def remove_chat_tasks(ctxids: list[str]) -> None:
    """Reload the scheduler once, then drop the tasks of every removed chat."""
    scheduler = TaskScheduler.get()
    await scheduler.reload()
    for ctxid in ctxids:
        for task in scheduler.get_tasks_by_context_id(ctxid):
            await scheduler.remove_task_by_uuid(task.uuid)
