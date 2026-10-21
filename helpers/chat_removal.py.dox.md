# chat_removal.py DOX

## Purpose

- The one cleanup for removing a conversation, shared by `api/chat_remove.py` and `api/chat_remove_many.py`.

## Ownership

- `chat_removal.py` owns the runtime implementation; this file owns the notes on it.
- Functions:
  - `remove_chat_state(ctxid)` -- cancel the chat's running scheduler tasks (terminating the thread), reset a running context, `AgentContext.remove`, `persist_chat.remove_chat`.
  - `async remove_chat_tasks(ctxids)` -- reload the scheduler once, then remove every scheduler task bound to those chats.

## Runtime Contracts

- No ownership check here: callers must `assert_context_access` first (`AgentContext.use/remove` bypass `use_context`).
- Neither function marks state dirty; the caller calls `mark_dirty_all` once.
- Single delete calls `remove_chat_state` then `remove_chat_tasks([id])`, the order it always had.

## Verification

- `tests/test_chat_remove_many.py`, `tests/test_user_isolation.py`.

## Child DOX Index

No child DOX files.
