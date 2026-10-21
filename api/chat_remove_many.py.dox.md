# chat_remove_many.py DOX

## Purpose

- Delete several conversations in one request (Conversations panel multi-select).

## Ownership

- `chat_remove_many.py` owns the runtime implementation; this file owns the notes on it.
- Classes: `RemoveChats` (`ApiHandler`), `async process(self, input, request) -> Output`.

## Runtime Contracts

- POST `{contexts: [id...], include_subagents: bool}`. Permission `chat.use` (route entry `chat_remove_many` in `helpers/api_policy.py`).
- More than 200 ids, or a non-list / non-string payload, is rejected with an error before anything is removed. Duplicates are collapsed.
- Per id: unknown id or `assert_context_access` failure goes to `failed[]`; the batch never aborts.
- `include_subagents`: also removes, transitively, the caller's own conversations whose `output_data` has `parent_context_kind == "subordinate"` and a `parent_context_id` of a *removed* chat. A refused parent never cascades. When false, children are left alone.
- Response `{removed: [ids], failed: [{id, error}], subagents_removed: n}`; `removed` includes the sub-agent ids.
- Side effects: scheduler reloaded once, `mark_dirty_all` once, one `chat.remove_many` audit event (counts and ids only, never chat content).
- Cleanup per id is `helpers.chat_removal.remove_chat_state`; scheduler tasks go through `remove_chat_tasks`.

## Verification

- `tests/test_chat_remove_many.py`, `tests/test_api_permission_coverage.py`.

## Child DOX Index

No child DOX files.
