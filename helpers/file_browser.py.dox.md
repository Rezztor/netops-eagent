# file_browser.py DOX

## Purpose

- Own the `file_browser.py` helper module.
- This module builds safe file-browser views over allowed filesystem roots.
- Keep this file-level DOX profile synchronized with `file_browser.py` because this directory is intentionally flat.

## Ownership

- `file_browser.py` owns the runtime implementation.
- `file_browser.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `FileBrowser` (no explicit base class)
  - `save_file_b64(self, current_path: str, filename: str, base64_content: str)`
  - `save_files(self, files: List, current_path: str=...) -> Tuple[List[str], List[str]]`
  - `delete_file(self, file_path: str) -> bool`
  - `rename_item(self, file_path: str, new_name: str) -> bool`
  - `move_items(self, file_paths: List[str], destination_path: str) -> List[str]`
  - `create_folder(self, parent_path: str, folder_name: str) -> bool`
  - `save_text_file(self, file_path: str, content: str) -> bool`
  - `get_files(self, current_path: str=...) -> Dict`
  - `get_full_path(self, file_path: str, allow_dir: bool=...) -> str`

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: filesystem reads, filesystem writes, filesystem deletion, subprocess/runtime control, settings/state persistence.
- Imported dependency areas include: `base64`, `datetime`, `helpers`, `helpers.localization`, `helpers.print_style`, `helpers.security`, `os`, `pathlib`, `shutil`, `subprocess`, `typing`.

- **Storage limits.** `save_files` and `save_file_b64` refuse a file over `MAX_FILE_SIZE` (100 MiB) and stop at the account quota. `ACCOUNT_QUOTA_BYTES` (default 50 GiB, `REZZTOR_ACCOUNT_QUOTA_BYTES` overrides) bounds what one account holds across its working directory and `usr/users/<id>`; `account_usage(workdir_root=None)` walks both with `os.scandir` without following links (O(files) per call), `check_account_quota(incoming)` raises `QuotaExceeded` (a `ValueError`), and `stream_size(stream)` measures an upload. Without a user scope only the working-directory root is counted. `quota_lock()` is the current account's re-entrant lock (process-level: one uvicorn worker holds every request); every upload path holds it from its check until its writes are done, so N concurrent uploads cannot each pass against the same free space. Hold it around synchronous code only. An archive extraction is checked after it is unpacked and is not under the lock. Callers: this class, `api/upload.py`, `api/extract_work_dir_archive.py`. `tests/test_account_storage_quota.py` covers this.
- A request path with a leading slash is read as relative to the browser root when it names something that exists there (the handlers prepend the slash to listing paths); otherwise it stays filesystem-absolute and is refused by the containment check. The root itself can be listed but not deleted or renamed.

## Key Concepts

- Important called helpers/classes observed in the source: `Path`, `files.get_abs_path`, `self._get_file_extension`, `file.seek`, `file.tell`, `resolve`, `os.makedirs`, `os.path.exists`, `full_path.with_name`, `new_path.exists`, `os.rename`, `target_dir.exists`, `filename.rsplit.lower`, `subprocess.run`, `result.stdout.strip.split`, `self._get_files_via_ls`, `files.exists`, `ValueError`, `str.startswith`, `file.write`.
- Multi-item moves validate every source and target before renaming, reject collisions and directory self-nesting, preserve symlink objects, and best-effort roll back earlier renames if a later rename fails.
- **The root is the boundary.** `FileBrowser()` is rooted at the caller's own working directory (`helpers.workdir.scope_to_user`); `base_dir=` overrides it for callers that own their tree, and no API handler passes it. It was `"/"`, with every containment check written as `str(path).startswith(str(self.base_dir))` -- true for every path on the box. A `chat.use` account could list `/`, read `usr/.env` and reach other tenants' files. Containment now goes through `_contained()`, which resolves first and compares with `Path.is_relative_to`, because a string prefix is not a path boundary: `/home/u-evil` passes a `startswith` test for `/home/u`. `tests/test_file_browser_navigation.py` covers the escape, the `..` and the sibling-prefix case.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_download_toast_regressions.py`
  - `tests/test_office_document_store.py`
  - `tests/test_file_browser_navigation.py`

## Child DOX Index

No child DOX files.
