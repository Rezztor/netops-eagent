# Time Travel Plugin DOX

## Purpose

- Own Rezztor EAgent workspace history, diff inspection, travel, snapshots, and revert for selectable Rezztor EAgent workdir/project workspaces under `/eagent/usr`.

## Ownership

- `helpers/time_travel.py` owns history storage, diff, travel, snapshot, preview, and revert mechanics.
- `api/` owns selectable workspace list, history list, diff, preview, revert, snapshot, and travel endpoints.
- `webui/` owns the time-travel panel, workspace selector, store, main surface, and thumbnail.
- `plugin.yaml` and `extensions/` own metadata and hook contributions.

## Local Contracts

- Keep history operations scoped to Rezztor EAgent-owned workdir/project workspaces.
- Revert and travel operations must avoid unintended writes outside managed workspace paths.
- `history_revert` and `history_travel` on a project's workspace need `settings.manage` (`project_rewrite_refusal`): a project's tracked files include its instructions, knowledge and skills, which reach every account, and changing a project is administration (`api/projects.py`). An account's own working folder is its own to travel. Listing, diff, preview and snapshot stay open: they change no file of the project.
- Preserve enough metadata for clear preview and diff inspection before destructive actions.

- The retention sweep runs with no account in scope and must count every account's own working directory as live, not only the shared base.
- The usr watchdog also runs with no account in scope. `resolve_workspace_for_path_hint` must map `<workdir_base>/<user_id>/...` to that account's own workspace, never the shared base, so one shadow repository cannot hold every account's working folder.

## Work Guidance

- Coordinate API and UI changes so users can inspect effects before applying travel or revert operations.

## Verification

- Smoke-test workspace selection, snapshot, list, diff, preview, travel, and revert flows after changes.

## Child DOX Index

No child DOX files.
