"""Shared context helper used by both ApiHandler and WsHandler."""

import threading
from typing import Union

ThreadLockType = Union[threading.Lock, threading.RLock]


class ContextAccessDenied(Exception):
    """Raised when a caller asks for a conversation owned by someone else."""


def assert_context_access(ctxid: str) -> None:
    """Raise unless the current user may act on ctxid.

    For endpoints that operate on a conversation without resolving it through
    use_context -- removing, resetting, terminating -- where reaching for
    AgentContext directly would skip the ownership check.
    """
    from agent import AgentContext
    from helpers import user_scope

    if not ctxid:
        return
    context = AgentContext.get(ctxid)
    if context and not context.is_owned_by(user_scope.get_current_user_id()):
        raise ContextAccessDenied(f"Context {ctxid} not found")


def use_context(lock: ThreadLockType, ctxid: str, create_if_not_exists: bool = True):
    from agent import AgentContext
    from helpers import projects, user_scope
    from initialize import initialize_agent

    # Single chokepoint for reaching a conversation. Every API and WebSocket
    # handler resolves contexts through here, so the ownership check lives here
    # rather than being repeated -- and forgotten -- in individual handlers.
    current_user_id = user_scope.get_current_user_id()

    with lock:
        if not ctxid:
            first = AgentContext.first_for_user(current_user_id)
            if first:
                AgentContext.use(first.id)
                return first
            context = AgentContext(config=initialize_agent(), set_current=True)
            projects.reconcile_agent_profile(
                context, projects.get_context_project_name(context)
            )
            return context

        got = AgentContext.get(ctxid)
        if got:
            if not got.is_owned_by(current_user_id):
                # Deliberately identical to the not-found case below, so probing
                # ids cannot distinguish "someone else's chat" from "no such
                # chat".
                raise ContextAccessDenied(f"Context {ctxid} not found")
            AgentContext.use(ctxid)
            return got

        if create_if_not_exists:
            context = AgentContext(
                config=initialize_agent(), id=ctxid, set_current=True
            )
            projects.reconcile_agent_profile(
                context, projects.get_context_project_name(context)
            )
            return context
        raise ContextAccessDenied(f"Context {ctxid} not found")
