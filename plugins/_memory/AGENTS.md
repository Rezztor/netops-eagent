# Memory Plugin DOX

## Purpose

- Own persistent memory, knowledge import, vector indexing, and memory management UI.

## Ownership

- `helpers/memory.py` owns FAISS store loading, embedding metadata, and knowledge preload.
- `helpers/knowledge_import.py` and `helpers/memory_consolidation.py` own import and consolidation behavior.
- `tools/` owns memory save/load/delete/forget and behavior adjustment tools.
- `api/` and `webui/` own memory dashboard and knowledge reindex/import flows.
- `prompts/`, `default_config.yaml`, and `plugin.yaml` own memory and behavior-tool prompts, defaults, and metadata.

## Local Contracts

- Keep memory scoped by configured subdirectory/context.
- Preserve embedding metadata needed to rebuild indexes safely.
- When the embedding model changes, stored memories are embedded again the first time their area is opened, and a `kvp` marker (`memory_embedding_<area>`) records the model they were embedded with. For a person's own area the marker is that account's (`kvp.set_user_persistent`), because only that account's rows are visible to be indexed; for a project's area, whose rows are shared, it is the instance's. `tests/test_memory_store.py` covers both.
- `memory_load` accepts numeric `threshold` and `limit` values as native numbers or numeric strings and coerces them before vector search.
- Avoid storing transient action-history noise as durable memory.
- Behaviour rules (`behaviour_adjustment`, `extensions/python/system_prompt/_20_behaviour_prompt.py`) are the person's, also inside a project: both resolve `behaviour.md` with `memory.get_user_memory_subdir_abs`, never the project's shared memory folder, because the tool needs no permission and that file goes in front of the reader's system prompt. A project's old `.eaproj/memory/behaviour.md` is not read (a warning says so once); rules for a whole project belong in its instructions, which only an administrator changes. `tests/test_behaviour_rules_per_user.py` covers this.

- A memory area name is one path segment (or `projects/<name>`); the filter expression compares metadata and cannot access attributes or call methods.
- The dashboard's `area` filter must match `[A-Za-z0-9_-]+` before it is formatted into a filter expression; anything else is refused (`tests/test_memory_dashboard_area.py`).
- `get_existing_memory_subdirs` lists areas from the `memories` table (RLS-scoped), not from an on-disk `index.faiss` file.
- Consolidation REPLACE checks each memory to be removed against the score it had in the search (`ConsolidationResult.shown_similarity`, filled from the memories shown to the model). The score is never stored, so it is not on a row read again by id. A memory the model was not shown has no score and is never replaced.

- A project's memories are shared to read and changed only by their writer (migration 0025, ADR 0088). `PgVectorStore.adelete` returns the ids actually deleted and callers report only those; `writable_ids` is the policy's test, and consolidation shows the model only writable memories. Re-embedding (`areembed`) and replacing imported knowledge (`adelete_knowledge`) are the only automatic writes that run as the owner role in a project's area; the third is the dashboard's administrator path (`unowned_too=True` on `delete_documents_by_ids` / `update_documents`), which lets a `settings.manage` holder edit or delete project rows with no writer (from before 0025) and nothing else. A row's `written_by`, `scope`, `area` and `owner_id` never change after the insert, for any role (migration 0027's trigger); updates set content, metadata and embedding only. `tests/test_memory_store.py` covers all of it.

## Work Guidance

- Coordinate tool, prompt, and consolidation changes so saved memories remain useful and bounded.

## Verification

- Smoke-test save, recall, delete, dashboard search/update, and knowledge import/reindex after changes.

## Child DOX Index

No child DOX files.
