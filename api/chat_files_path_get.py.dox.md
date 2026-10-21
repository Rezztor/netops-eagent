# chat_files_path_get.py DOX

## Purpose

- Own the `chat_files_path_get.py` API endpoint.
- This module handles chat files path get requests.
- Keep this file-level DOX profile synchronized with `chat_files_path_get.py` because this directory is intentionally flat.

## Ownership

- `chat_files_path_get.py` owns the runtime implementation.
- `chat_files_path_get.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `GetChatFilesPath` (`ApiHandler`)
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `GetChatFilesPath` is an `ApiHandler`.
- `GetChatFilesPath` defines `process(...)`.
- Returns the active project's folder for a project chat, otherwise **the chat owner's own working directory**, `helpers.workdir.get_workdir_path(context.owner_id)` (`<base>/<user_id>`, container-normalised; the requesting account when the chat has no owner). It used to return the configured shared base, which holds every account's folder (audit 6 M4).
- Observed side-effect areas: none; reads settings through `helpers.workdir`.
- Imported dependency areas include: `helpers`, `helpers.api`.

## Key Concepts

- Important called helpers/classes observed in the source: `self.use_context`, `projects.get_context_project_name`, `Exception`, `files.normalize_container_path`, `projects.get_project_folder`, `workdir.get_workdir_path`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- `tests/test_workdir_extras_scope.py` covers the prompt side of the same rule.

## Child DOX Index

No child DOX files.
