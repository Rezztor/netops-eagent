# workdir.py DOX

## Purpose

- Own the `workdir.py` helper module.
- This module resolves the agent's working directory for the current user, appending a per-user subdirectory to the administrator-configured `workdir_path` base so accounts cannot read, overwrite, or delete each other's agent-executed files.
- Keep this file-level DOX profile synchronized with `workdir.py` because this directory is intentionally flat.

## Ownership

- `workdir.py` owns the runtime implementation.
- `workdir.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
  - `scope_to_user(base: str, user_id: str | None = None) -> str`: append the owner's id segment to a configured base path; returns `base` unchanged if it is empty or no safe owner can be resolved.
  - `get_workdir_base() -> str`: the configured `workdir_path` setting, or `usr/workdir` (dockerized path) if unset.
  - `get_workdir_path(user_id: str | None = None) -> str`: `scope_to_user(get_workdir_base(), user_id)`, container-normalised.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- `scope_to_user` is applied unconditionally, not only to the default path — an administrator who repoints `workdir_path` at a mounted volume still gets per-user separation automatically, rather than silently losing it by changing a setting that says nothing about users.
- When no resolvable, safe owner exists (`user_scope.is_safe_user_id` fails), `scope_to_user` returns the **base path unchanged** rather than inventing a directory or raising — callers that must not share a workdir across users are expected to fail closed elsewhere, not rely on this function to refuse.
- `scope_to_user(base, ...)` is kept separate from reading the `workdir_path` setting deliberately: several callers resolve the base through their own indirection (e.g. for projects, or for testability) rather than always reading settings directly, and routing everyone through a single settings read here would bypass that.
- Observed side-effect areas: none — path composition only; does not create directories or touch the filesystem itself.
- Imported dependency areas include: `helpers.files`, `helpers.user_scope`, `helpers.settings` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `user_scope.get_current_user_id`, `user_scope.is_safe_user_id`, `files.get_abs_path`, `files.fix_dev_path`, `files.get_abs_path_dockerized`, `files.normalize_container_path`, `settings.get_settings`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

- `user_file_roots(user_id=None)` lists the folders a path from the model or a request may point into: the account's working directory, its uploads, downloads and skills, and the projects folder. `confine_path(path, user_id=None, also=())` returns the real path once it is proven inside one of them, and raises `RepairableException` otherwise (`UserScopeError` with no account in scope). Relative paths are read from the working directory; `realpath` runs before the check; a project's `.eaproj` and `.git` are refused (`PROJECT_PRIVATE_PARTS`, `is_project_private(real_path)`): the first holds secrets and Python the application loads, the second a configuration git obeys when the application asks for a project's status. Every tool and endpoint that opens a model- or request-named file in the application container goes through it. `cannot_change_project_instructions(real_path, user_id=None)` is the one rule for a write beyond that: a project's `AGENTS.md` (`PROJECT_INSTRUCTION_NAMES`, at the root or in any folder below) is read into the prompt of every account that opens the project, so only a holder of `settings.manage` changes it; the text editor's write and patch and the document editor ask it, and reading stays open. `confine_read_path(path, user_id=None)` is the same check for a caller that only reads, and additionally lets through the account's own saved tool results, `chats/<conversation>/messages/<n>.txt` (exactly that shape, after `realpath`): the include marker and the text editor's read use it; no write does.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Any new caller that resolves a workdir base outside `get_workdir_base()` must still pass it through `scope_to_user` before using it, to keep per-user isolation.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_user_isolation.py`

## Child DOX Index

No child DOX files.
