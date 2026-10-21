# rename_work_dir_file.py DOX

## Purpose

- Own the `rename_work_dir_file.py` API endpoint.
- This module handles workdir file operations for rename work dir file.
- Keep this file-level DOX profile synchronized with `rename_work_dir_file.py` because this directory is intentionally flat.

## Ownership

- `rename_work_dir_file.py` owns the runtime implementation.
- `rename_work_dir_file.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `RenameWorkDirFile` (`ApiHandler`)
  - `async process(self, input: Input, request: Request) -> Output`
- Top-level functions:
- `async rename_item(file_path: str, new_name: str) -> bool`
- `async create_folder(parent_path: str, folder_name: str) -> bool`
- `async move_items(file_paths: list[str], destination_path: str) -> list[str]`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `RenameWorkDirFile` is an `ApiHandler`.
- `RenameWorkDirFile` defines `process(...)`.
- Observed side-effect areas: filesystem writes.
- Imported dependency areas include: `api`, `helpers`, `helpers.api`, `helpers.file_browser`, `posixpath`.

## Key Concepts

- Important called helpers/classes observed in the source: `FileBrowser`, `browser.rename_item`, `browser.create_folder`, `browser.move_items`, `strip`, `runtime.call_development_function`, `posixpath.join`, `file_path.startswith`, `extension.call_extensions_async`, `str.rstrip`, `posixpath.dirname`.
- The `move` action accepts `paths` plus `destinationPath`, emits the standard mutation hook, and returns the refreshed `currentPath` listing used by drag-and-drop clients.
- `create-folder` treats an empty `parentPath` as the working directory itself, not as a missing value. It returned `{"error": "Parent path is required"}` for it, which was harmless only while `get_work_dir_files` answered `"/eagent"` for the default view -- a non-empty string that happened to satisfy the guard. Once the browser was rooted at the caller's own directory that path became `""`, and creating a folder at the root of an empty account became impossible through the interface. What keeps accepting it safe is `FileBrowser.resolve_within`, which resolves and proves containment; `tests/test_file_browser_navigation.py` covers both halves.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Run `pytest tests/test_file_browser_navigation.py` and smoke-test folder and Up-button drops in the WebUI.

## Child DOX Index

No child DOX files.
